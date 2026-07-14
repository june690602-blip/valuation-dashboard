"""Regression tests for the investment risk-profile model.

The tests deliberately focus on public outputs and documented guardrails.  They
make changes to questionnaire wording harmless while keeping scoring, API
serialization, and the educational CML calculations stable.
"""

from __future__ import annotations

import json
import math
import unittest

import numpy as np

from src.analysis.risk_profile import (
    DIMENSIONS,
    LEVELS,
    PROFILE_SCHEMA_VERSION,
    QUESTIONS,
    grade,
    indifference_curve,
    optimal_risky_share,
    profile_to_dict,
    risk_aversion_from_score,
    risk_profile_config,
    tangency_point,
)


class RiskProfileConfigTests(unittest.TestCase):
    def test_public_config_matches_canonical_questionnaire(self) -> None:
        config = risk_profile_config()

        self.assertEqual(PROFILE_SCHEMA_VERSION, 2)
        self.assertEqual(config["schema_version"], PROFILE_SCHEMA_VERSION)
        self.assertEqual(config["estimated_minutes"], 2)
        self.assertEqual(config["question_count"], 8)
        self.assertEqual(config["question_count"], len(QUESTIONS))
        self.assertEqual(len(config["questions"]), len(QUESTIONS))
        self.assertEqual(len(config["levels"]), len(LEVELS))

        question_ids = [question["id"] for question in config["questions"]]
        self.assertEqual(len(question_ids), len(set(question_ids)))
        self.assertEqual(question_ids, [question.id for question in QUESTIONS])

        dimension_keys = [dimension["key"] for dimension in config["dimensions"]]
        self.assertEqual(dimension_keys, list(DIMENSIONS))
        self.assertEqual(
            {question["dimension"] for question in config["questions"]},
            set(DIMENSIONS),
        )
        for question in config["questions"]:
            self.assertEqual(len(question["options"]), 5)
            self.assertTrue(question["text"].strip())
            self.assertTrue(question["chapter"].strip())

    def test_dimension_weights_form_a_complete_weighted_score(self) -> None:
        self.assertAlmostEqual(
            sum(float(meta["weight"]) for meta in DIMENSIONS.values()),
            1.0,
        )

    def test_level_thresholds_are_ordered_and_allocations_are_valid(self) -> None:
        thresholds = [level.min_score for level in LEVELS]
        self.assertEqual(thresholds, sorted(thresholds))

        for level in LEVELS:
            allocation = dict(level.allocation)
            ranges = dict(level.allocation_range)
            self.assertEqual(sum(allocation.values()), 100)
            self.assertEqual(set(allocation), set(ranges))

            for asset, exact_share in allocation.items():
                low, high = ranges[asset]
                self.assertLessEqual(0, low)
                self.assertLessEqual(low, exact_share)
                self.assertLessEqual(exact_share, high)
                self.assertLessEqual(high, 100)


class RiskProfileScoringTests(unittest.TestCase):
    def test_minimum_answers_produce_the_most_conservative_profile(self) -> None:
        profile = grade([0] * len(QUESTIONS))

        self.assertEqual(profile.raw_score, 0)
        self.assertEqual(profile.score, 0)
        self.assertEqual(profile.level, 1)
        self.assertEqual(profile.dimension_scores, {key: 0 for key in DIMENSIONS})
        self.assertEqual(profile.A, 9.0)
        self.assertEqual(sum(profile.allocation.values()), 100)

    def test_maximum_answers_produce_the_highest_risk_profile(self) -> None:
        profile = grade([4] * len(QUESTIONS))

        self.assertEqual(profile.raw_score, 100)
        self.assertEqual(profile.score, 100)
        self.assertEqual(profile.level, len(LEVELS))
        self.assertEqual(profile.dimension_scores, {key: 100 for key in DIMENSIONS})
        self.assertEqual(profile.A, 1.3)
        self.assertIsNone(profile.guardrail_note)

    def test_midpoint_answers_center_every_dimension(self) -> None:
        profile = grade([2] * len(QUESTIONS))

        self.assertEqual(profile.raw_score, 50)
        self.assertEqual(profile.score, 50)
        self.assertEqual(profile.level, 3)
        self.assertEqual(profile.dimension_scores, {key: 50 for key in DIMENSIONS})
        self.assertEqual(profile.consistency, "고르게 나타남")
        self.assertEqual(profile.A, 5.15)

    def test_capacity_hard_guardrail_caps_an_otherwise_high_profile(self) -> None:
        profile = grade([0] + [4] * (len(QUESTIONS) - 1))

        self.assertEqual(profile.raw_score, 82)
        self.assertEqual(profile.level, 2)
        self.assertEqual(profile.score, LEVELS[2].min_score - 1)
        self.assertIsNotNone(profile.guardrail_note)
        self.assertLess(profile.score, profile.raw_score)
        self.assertEqual(profile.A, risk_aversion_from_score(profile.score))

    def test_low_capacity_dimension_caps_profile_at_level_three(self) -> None:
        # Both capacity answers score 2/5: low, but neither triggers the
        # stronger one-answer hard guardrail.
        profile = grade([1, 1, 4, 4, 4, 4, 4, 4])

        self.assertEqual(profile.dimension_scores["capacity"], 25)
        self.assertGreaterEqual(profile.raw_score, LEVELS[3].min_score)
        self.assertEqual(profile.level, 3)
        self.assertEqual(profile.score, LEVELS[3].min_score - 1)
        self.assertIsNotNone(profile.guardrail_note)

    def test_low_knowledge_caps_profile_at_level_three(self) -> None:
        profile = grade([4, 4, 0, 4, 4, 4, 4, 4])

        self.assertEqual(profile.dimension_scores["knowledge"], 0)
        self.assertEqual(profile.raw_score, 85)
        self.assertEqual(profile.level, 3)
        self.assertEqual(profile.score, LEVELS[3].min_score - 1)
        self.assertIsNotNone(profile.guardrail_note)
        self.assertEqual(profile.A, risk_aversion_from_score(profile.score))

    def test_certainty_equivalent_is_centered_and_half_weighted(self) -> None:
        neutral = grade([2] * len(QUESTIONS))
        lower = grade([2, 2, 2, 2, 2, 0, 2, 2])
        higher = grade([2, 2, 2, 2, 2, 4, 2, 2])

        self.assertEqual(neutral.dimension_scores["tolerance"], 50)
        self.assertEqual(lower.dimension_scores["tolerance"], 40)
        self.assertEqual(higher.dimension_scores["tolerance"], 60)

    def test_dimension_scores_always_stay_in_zero_to_one_hundred_range(self) -> None:
        answer_sets = (
            [0] * len(QUESTIONS),
            [4] * len(QUESTIONS),
            [0, 4, 1, 3, 2, 4, 0, 3],
        )
        for answers in answer_sets:
            with self.subTest(answers=answers):
                scores = grade(answers).dimension_scores
                self.assertEqual(set(scores), set(DIMENSIONS))
                self.assertTrue(all(0 <= score <= 100 for score in scores.values()))

    def test_validation_rejects_wrong_answer_count(self) -> None:
        for answers in ([], [0] * (len(QUESTIONS) - 1), [0] * (len(QUESTIONS) + 1)):
            with self.subTest(length=len(answers)), self.assertRaises(ValueError):
                grade(answers)

    def test_validation_rejects_bool_and_non_integer_indices(self) -> None:
        invalid_values = (True, False, 1.0, "1", None)
        for invalid in invalid_values:
            answers = [0] * len(QUESTIONS)
            answers[3] = invalid  # type: ignore[list-item]
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                grade(answers)

    def test_validation_rejects_negative_and_out_of_range_indices(self) -> None:
        for invalid in (-1, 5, 99):
            answers = [0] * len(QUESTIONS)
            answers[0] = invalid
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                grade(answers)


class RiskProfileSerializationTests(unittest.TestCase):
    def test_serialization_is_json_safe_and_preserves_assessed_value(self) -> None:
        profile = grade([2] * len(QUESTIONS))
        payload = profile_to_dict(profile)

        self.assertEqual(payload["schema_version"], PROFILE_SCHEMA_VERSION)
        self.assertEqual(payload["assessed_A"], profile.A)
        self.assertEqual(payload["A"], profile.A)
        self.assertNotIn("scenario_A", payload)
        self.assertEqual(payload["dimension_scores"], profile.dimension_scores)
        self.assertEqual(payload["allocation"], profile.allocation)
        self.assertEqual(payload["nickname"], profile.archetype)
        self.assertEqual(payload["emoji"], profile.symbol)
        self.assertEqual(
            payload["allocation_range"],
            {asset: list(bounds) for asset, bounds in profile.allocation_range.items()},
        )
        json.dumps(payload, ensure_ascii=False)

    def test_every_scored_profile_uses_its_level_allocation(self) -> None:
        for answer_index in range(5):
            profile = grade([answer_index] * len(QUESTIONS))
            level = LEVELS[profile.level - 1]
            with self.subTest(answer_index=answer_index, level=profile.level):
                self.assertEqual(profile.allocation, dict(level.allocation))
                self.assertEqual(profile.allocation_range, dict(level.allocation_range))
                self.assertEqual(sum(profile.allocation.values()), 100)


class RiskAversionAndCmlTests(unittest.TestCase):
    def test_risk_aversion_is_monotonically_decreasing(self) -> None:
        scores = list(range(0, 101, 5))
        values = [risk_aversion_from_score(score) for score in scores]

        self.assertEqual(values[0], 9.0)
        self.assertEqual(risk_aversion_from_score(50), 5.15)
        self.assertEqual(values[-1], 1.3)
        self.assertTrue(all(left >= right for left, right in zip(values, values[1:])))

    def test_risk_aversion_clamps_bounds_and_rejects_non_finite_values(self) -> None:
        self.assertEqual(risk_aversion_from_score(-100), 9.0)
        self.assertEqual(risk_aversion_from_score(200), 1.3)
        for invalid in (math.nan, math.inf, -math.inf):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                risk_aversion_from_score(invalid)

    def test_optimal_risky_share_matches_mean_variance_formula(self) -> None:
        share = optimal_risky_share(er_m=0.08, rf=0.02, sigma_m=0.20, A=4.0)
        self.assertAlmostEqual(share, 0.375)

    def test_optimal_risky_share_handles_invalid_parameters(self) -> None:
        self.assertEqual(optimal_risky_share(0.08, 0.02, 0.0, 4.0), 0.0)
        self.assertEqual(optimal_risky_share(0.08, 0.02, 0.2, 0.0), 0.0)
        self.assertEqual(optimal_risky_share(0.08, 0.02, -0.2, 4.0), 0.0)
        self.assertEqual(optimal_risky_share(0.08, 0.02, 0.2, -1.0), 0.0)
        for invalid_args in (
            (math.nan, 0.02, 0.2, 4.0),
            (0.08, math.inf, 0.2, 4.0),
            (0.08, 0.02, math.nan, 4.0),
            (0.08, 0.02, 0.2, math.inf),
        ):
            with self.subTest(args=invalid_args), self.assertRaises(ValueError):
                optimal_risky_share(*invalid_args)

    def test_tangency_point_obeys_cml_and_mrs_conditions(self) -> None:
        er_m, rf, sigma_m, aversion = 0.08, 0.02, 0.20, 4.0
        point = tangency_point(er_m, rf, sigma_m, aversion)

        self.assertAlmostEqual(point["y_star"], 0.375)
        self.assertAlmostEqual(point["sigma_p"], point["y_star"] * sigma_m)
        self.assertAlmostEqual(
            point["er_p"],
            rf + point["y_star"] * (er_m - rf),
        )
        self.assertAlmostEqual(point["utility"], 0.03125)
        self.assertAlmostEqual(point["sharpe"], (er_m - rf) / sigma_m)
        self.assertAlmostEqual(point["mrs"], point["sharpe"])

    def test_zero_volatility_tangency_is_finite(self) -> None:
        point = tangency_point(0.08, 0.02, 0.0, 4.0)

        self.assertEqual(point["y_star"], 0.0)
        self.assertEqual(point["sigma_p"], 0.0)
        self.assertEqual(point["er_p"], 0.02)
        self.assertEqual(point["sharpe"], 0.0)
        self.assertTrue(all(math.isfinite(value) for value in point.values()))

    def test_indifference_curve_matches_quadratic_utility_equation(self) -> None:
        sigmas = np.array([0.0, 0.1, 0.2])
        curve = indifference_curve(A=4.0, u_star=0.03, sigmas=sigmas)

        np.testing.assert_allclose(curve, np.array([0.03, 0.05, 0.11]))
        self.assertEqual(curve.shape, sigmas.shape)


if __name__ == "__main__":
    unittest.main()
