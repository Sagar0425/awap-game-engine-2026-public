import random
from collections import deque
from typing import Tuple, Optional

from game_constants import Team, TileType, FoodType, ShopCosts, GameConstants
from robot_controller import RobotController
from item import Pan, Plate, Food
from order_priority import build_order_priority_queue


class BotPlayer:
    def __init__(self, map_copy):
        self.map = map_copy
        self.assembly_counter = None
        self.cooker_loc = None
        self.my_bot_id = None
        self.current_order_id = None
        self.has_printed_priority = False
        self.seen_order_ids = set()
        self.seen_active_order_ids = set()

        # FSM state
        self.state = 0

    # ------------------------------------------------------------
    # PATHFINDING
    # ------------------------------------------------------------
    def get_bfs_path(self, controller: RobotController,
                     start: Tuple[int, int],
                     target_predicate) -> Optional[Tuple[int, int]]:

        queue = deque([(start, [])])
        visited = {start}
        w, h = self.map.width, self.map.height

        while queue:
            (x, y), path = queue.popleft()
            tile = controller.get_tile(controller.get_team(), x, y)

            if target_predicate(x, y, tile):
                return (0, 0) if not path else path[0]

            for dx, dy in [(1,0), (-1,0), (0,1), (0,-1)]:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in visited:
                    if controller.get_map().is_tile_walkable(nx, ny):
                        visited.add((nx, ny))
                        queue.append(((nx, ny), path + [(dx, dy)]))
        return None

    def move_towards(self, controller, bot_id, tx, ty) -> bool:
        state = controller.get_bot_state(bot_id)
        bx, by = state['x'], state['y']

        if max(abs(bx - tx), abs(by - ty)) <= 1:
            return True

        step = self.get_bfs_path(
            controller,
            (bx, by),
            lambda x, y, t: max(abs(x - tx), abs(y - ty)) <= 1
        )

        if step and step != (0, 0):
            controller.move(bot_id, step[0], step[1])
        return False

    # ------------------------------------------------------------
    # TILE SEARCH
    # ------------------------------------------------------------
    def find_nearest_tile(self, controller, bx, by, tile_name):
        best = None
        best_dist = 9999
        m = controller.get_map()

        for x in range(m.width):
            for y in range(m.height):
                tile = m.tiles[x][y]
                if tile.tile_name == tile_name:
                    d = max(abs(bx - x), abs(by - y))
                    if d < best_dist:
                        best_dist = d
                        best = (x, y)
        return best

    # ------------------------------------------------------------
    # MAIN TURN
    # ------------------------------------------------------------
    def play_turn(self, controller: RobotController):
        bots = controller.get_team_bot_ids()
        if not bots:
            return
        orders = controller.get_orders()
        prep_time_by_food = {}
        for food_type in FoodType:
            prep_time = 0
            if food_type.can_chop:
                prep_time += 1
            if food_type.can_cook:
                prep_time += GameConstants.COOK_PROGRESS
            prep_time_by_food[food_type.food_name] = float(prep_time)
            if food_type.food_name == "ONIONS":
                prep_time_by_food["ONION"] = float(prep_time)
        prioritized = build_order_priority_queue(
            orders,
            controller.get_turn(),
            prep_time_by_food=prep_time_by_food,
            default_prep_time=1.0,
            value_weight=2.0,
            urgency_weight=2.0,
            slack_weight=1.5,
            activation_weight=1.0,
            allow_inactive=True,
            allow_claimed=False,
        )
        current_ids = {order.get("order_id") for order in prioritized if order.get("order_id") is not None}
        current_active_ids = {
            order.get("order_id")
            for order in prioritized
            if order.get("order_id") is not None and order.get("is_active")
        }
        is_first_print = not self.has_printed_priority
        has_new_orders = bool(current_ids - self.seen_order_ids)
        has_new_active = bool(current_active_ids - self.seen_active_order_ids)
        if is_first_print or (has_new_orders and controller.get_turn() > 0) or has_new_active:
            label = "Initial" if is_first_print else "Updated"
            print(f"{label} priority queue (turn {controller.get_turn()}):")
            for order in prioritized:
                print(
                    f"order_id={order.get('order_id')} "
                    f"required={order.get('required')} "
                    f"score={order.get('priority_score'):.3f} "
                    f"turns_left={order.get('turns_left')} "
                    f"estimated_prep={order.get('estimated_prep'):.1f} "
                    f"slack={order.get('slack'):.1f}"
                )
            self.has_printed_priority = True
        self.seen_order_ids |= current_ids
        self.seen_active_order_ids |= current_active_ids
        active_order = next((o for o in prioritized if o.get("is_active")), None)
        selected = active_order or (prioritized[0] if prioritized else None)
        self.current_order_id = selected["order_id"] if selected else None

        self.my_bot_id = bots[0]
        bot_id = self.my_bot_id
        bot = controller.get_bot_state(bot_id)
        bx, by = bot['x'], bot['y']
        holding = bot.get('holding')

        if self.assembly_counter is None:
            self.assembly_counter = self.find_nearest_tile(controller, bx, by, "COUNTER")
        if self.cooker_loc is None:
            self.cooker_loc = self.find_nearest_tile(controller, bx, by, "COOKER")

        if not self.assembly_counter or not self.cooker_loc:
            return

        cx, cy = self.assembly_counter
        kx, ky = self.cooker_loc

        # ------------------------------------------------------------
        # FSM
        # ------------------------------------------------------------

        # STATE 0 — check for pan
        if self.state == 0:
            tile = controller.get_tile(controller.get_team(), kx, ky)
            self.state = 2 if tile and isinstance(tile.item, Pan) else 1

        # STATE 1 — buy / place pan
        elif self.state == 1:
            if holding:
                if self.move_towards(controller, bot_id, kx, ky):
                    controller.place(bot_id, kx, ky)
                    self.state = 2
            else:
                sx, sy = self.find_nearest_tile(controller, bx, by, "SHOP")
                if self.move_towards(controller, bot_id, sx, sy):
                    if controller.get_team_money() >= ShopCosts.PAN.buy_cost:
                        controller.buy(bot_id, ShopCosts.PAN, sx, sy)

        # STATE 2 — buy meat
        elif self.state == 2:
            sx, sy = self.find_nearest_tile(controller, bx, by, "SHOP")
            if self.move_towards(controller, bot_id, sx, sy):
                if controller.get_team_money() >= FoodType.MEAT.buy_cost:
                    if controller.buy(bot_id, FoodType.MEAT, sx, sy):
                        self.state = 3

        # STATE 3 — place meat
        elif self.state == 3:
            if self.move_towards(controller, bot_id, cx, cy):
                controller.place(bot_id, cx, cy)
                self.state = 4

        # STATE 4 — chop
        elif self.state == 4:
            if self.move_towards(controller, bot_id, cx, cy):
                if controller.chop(bot_id, cx, cy):
                    self.state = 5

        # STATE 5 — pickup meat
        elif self.state == 5:
            if self.move_towards(controller, bot_id, cx, cy):
                if controller.pickup(bot_id, cx, cy):
                    self.state = 6

        # STATE 6 — cook meat
        elif self.state == 6:
            if self.move_towards(controller, bot_id, kx, ky):
                controller.place(bot_id, kx, ky)
                self.state = 8

        # STATE 8 — buy plate
        elif self.state == 8:
            sx, sy = self.find_nearest_tile(controller, bx, by, "SHOP")
            if self.move_towards(controller, bot_id, sx, sy):
                if controller.get_team_money() >= ShopCosts.PLATE.buy_cost:
                    if controller.buy(bot_id, ShopCosts.PLATE, sx, sy):
                        self.state = 9

        # STATE 9 — place plate
        elif self.state == 9:
            if self.move_towards(controller, bot_id, cx, cy):
                controller.place(bot_id, cx, cy)
                self.state = 10

        # STATE 10 — buy noodles
        elif self.state == 10:
            sx, sy = self.find_nearest_tile(controller, bx, by, "SHOP")
            if self.move_towards(controller, bot_id, sx, sy):
                if controller.get_team_money() >= FoodType.NOODLES.buy_cost:
                    if controller.buy(bot_id, FoodType.NOODLES, sx, sy):
                        self.state = 11

        # STATE 11 — add noodles
        elif self.state == 11:
            if self.move_towards(controller, bot_id, cx, cy):
                if controller.add_food_to_plate(bot_id, cx, cy):
                    self.state = 12

        # STATE 12 — wait for meat
        elif self.state == 12:
            if self.move_towards(controller, bot_id, kx, ky):
                tile = controller.get_tile(controller.get_team(), kx, ky)
                if tile and isinstance(tile.item, Pan) and tile.item.food:
                    if tile.item.food.cooked_stage == 1:
                        controller.take_from_pan(bot_id, kx, ky)
                        self.state = 13

        # STATE 13 — add meat
        elif self.state == 13:
            if self.move_towards(controller, bot_id, cx, cy):
                if controller.add_food_to_plate(bot_id, cx, cy):
                    self.state = 14

        # STATE 14 — pickup plate
        elif self.state == 14:
            if self.move_towards(controller, bot_id, cx, cy):
                if controller.pickup(bot_id, cx, cy):
                    self.state = 15

        # STATE 15 — submit
        elif self.state == 15:
            ux, uy = self.find_nearest_tile(controller, bx, by, "SUBMIT")
            if self.move_towards(controller, bot_id, ux, uy):
                if controller.submit(bot_id, ux, uy):
                    self.state = 17

        # STATE 17 — FULL RESET CLEANUP
        elif self.state == 17:

            # trash held items (except pan)
            if holding:
                if isinstance(holding, Pan):
                    if self.move_towards(controller, bot_id, kx, ky):
                        controller.place(bot_id, kx, ky)
                    return
                else:
                    tx, ty = self.find_nearest_tile(controller, bx, by, "TRASH")
                    if self.move_towards(controller, bot_id, tx, ty):
                        controller.trash(bot_id, tx, ty)
                    return

            # clear cooker
            tile = controller.get_tile(controller.get_team(), kx, ky)
            if tile and isinstance(tile.item, Pan) and tile.item.food:
                controller.take_from_pan(bot_id, kx, ky)
                return

            # clear counter
            tile = controller.get_tile(controller.get_team(), cx, cy)
            if tile and tile.item:
                controller.pickup(bot_id, cx, cy)
                return

            # HARD RESET — start loop again
            self.state = 0
