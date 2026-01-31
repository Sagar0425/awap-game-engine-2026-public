"""Order prioritization helpers for bots.

These helpers are intentionally stateless so bots can tune the weights.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from game_constants import FoodType, GameConstants


def _build_default_prep_time_by_food() -> Dict[str, float]:
    prep_time_by_food: Dict[str, float] = {}
    for food_type in FoodType:
        prep_time = 0
        if food_type.can_chop:
            prep_time += 1
        if food_type.can_cook:
            prep_time += GameConstants.COOK_PROGRESS
        prep_time_by_food[food_type.food_name] = float(prep_time)
        if food_type.food_name == "ONIONS":
            prep_time_by_food["ONION"] = float(prep_time)
    return prep_time_by_food


def _estimate_prep_turns(
    required: Iterable[str],
    prep_time_by_food: Optional[Dict[str, float]],
    default_prep_time: float,
) -> float:
    required_list = list(required)
    if prep_time_by_food is None:
        prep_time_by_food = _build_default_prep_time_by_food()

    return sum(prep_time_by_food.get(food, default_prep_time) for food in required_list)


def build_order_priority_queue(
    orders: List[Dict[str, Any]],
    current_turn: int,
    prep_time_by_food: Optional[Dict[str, float]] = None,
    default_prep_time: float = 1.0,
    value_weight: float = 1.0,
    urgency_weight: float = 1.0,
    slack_weight: float = 1.0,
    activation_weight: float = 1.0,
    allow_inactive: bool = False,
    allow_claimed: bool = False,
) -> List[Dict[str, Any]]:
    """Return orders sorted by descending priority.

    Priority heuristic balances:
    - Urgency (time remaining vs. estimated prep time).
    - Value (reward + penalty).
    - Avoiding downtime by preferring low slack orders.
    """

    prioritized: List[Dict[str, Any]] = []
    for order in orders:
        if order.get("completed_turn") is not None:
            continue

        is_active = order.get("is_active", False)
        if not allow_inactive and not is_active:
            continue
        if not allow_claimed and order.get("claimed_by") is not None:
            continue
        expires_turn = int(order["expires_turn"])
        time_left = expires_turn - current_turn
        if time_left < 0:
            continue
        required = order.get("required", [])
        estimated_prep = _estimate_prep_turns(required, prep_time_by_food, default_prep_time)
        slack = time_left - estimated_prep

        value = float(order.get("reward", 0)) + float(order.get("penalty", 0))
        value_rate = value / max(estimated_prep, 1.0)
        urgency = 1.0 / max(time_left, 1)
        slack_score = 1.0 / (1.0 + max(slack, 0.0))
        overdue_penalty = max(0.0, -slack)

        if is_active:
            score = (
                value_weight * value_rate
                + urgency_weight * urgency
                + slack_weight * slack_score
                - overdue_penalty
            )
        else:
            turns_until_active = max(0, int(order.get("created_turn", current_turn)) - current_turn)
            score = -activation_weight * turns_until_active

        prioritized.append(
            {
                **order,
                "priority_score": score,
                "turns_left": time_left,
                "estimated_prep": estimated_prep,
                "slack": slack,
            }
        )

    prioritized.sort(
        key=lambda o: (
            o["priority_score"],
            -o["turns_left"],
            o.get("created_turn", 0),
            o.get("order_id", 0),
        ),
        reverse=True,
    )
    return prioritized
