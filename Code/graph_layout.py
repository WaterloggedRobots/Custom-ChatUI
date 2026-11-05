"""The physics that spreads the link graph out: nodes push apart, links pull together.

Each step moves the nodes a little less (`heat` cools down), so the graph settles
and stops instead of drifting. Pinned nodes hold still and the rest arrange around
them. No Qt here, so it can be tested on its own (tests/test_chat_links.py).
"""

import math

REPULSION = 26000.0
LINK_LENGTH = 130.0
LINK_PULL = 0.05
CENTER_PULL = 0.006
DAMPING = 0.72
COOLING = 0.975
MAX_SPEED = 60.0
STOP_HEAT = 0.02


class Layout:
    def __init__(self):
        self.x, self.y = {}, {}
        self.vx, self.vy = {}, {}
        self.pinned = set()
        self.edges = []          # (node, node)
        self.heat = 1.0

    @property
    def settled(self) -> bool:
        return self.heat < STOP_HEAT

    def place(self, node, x, y, pinned=False):
        self.x[node], self.y[node] = float(x), float(y)
        self.vx[node], self.vy[node] = 0.0, 0.0
        if pinned:
            self.pinned.add(node)
        else:
            self.pinned.discard(node)

    def remove(self, node):
        for table in (self.x, self.y, self.vx, self.vy):
            table.pop(node, None)
        self.pinned.discard(node)

    def warm(self, heat=1.0):
        self.heat = max(self.heat, heat)

    def step(self, held=None):
        """One frame. `held` is the node being dragged, which the others react to but can't move."""
        nodes = list(self.x)
        fx = dict.fromkeys(nodes, 0.0)
        fy = dict.fromkeys(nodes, 0.0)
        xs, ys = self.x, self.y
        for i, a in enumerate(nodes):
            ax, ay = xs[a], ys[a]
            for b in nodes[i + 1:]:
                dx, dy = ax - xs[b], ay - ys[b]
                d2 = dx * dx + dy * dy
                if d2 < 1.0:                  # on top of each other: part them the same way every time
                    dx, dy, d2 = 1.0, (i % 3) - 1.0, 2.0
                force = REPULSION / d2
                d = math.sqrt(d2)
                push_x, push_y = force * dx / d, force * dy / d
                fx[a] += push_x
                fy[a] += push_y
                fx[b] -= push_x
                fy[b] -= push_y
        for a, b in self.edges:
            if a not in xs or b not in xs:
                continue
            dx, dy = xs[b] - xs[a], ys[b] - ys[a]
            d = math.hypot(dx, dy) or 1.0
            pull = (d - LINK_LENGTH) * LINK_PULL
            fx[a] += pull * dx / d
            fy[a] += pull * dy / d
            fx[b] -= pull * dx / d
            fy[b] -= pull * dy / d
        for n in nodes:
            if n in self.pinned or n == held:
                self.vx[n] = self.vy[n] = 0.0
                continue
            vx = (self.vx[n] + (fx[n] - xs[n] * CENTER_PULL) * self.heat) * DAMPING
            vy = (self.vy[n] + (fy[n] - ys[n] * CENTER_PULL) * self.heat) * DAMPING
            speed = math.hypot(vx, vy)
            if speed > MAX_SPEED:
                vx, vy = vx * MAX_SPEED / speed, vy * MAX_SPEED / speed
            self.vx[n], self.vy[n] = vx, vy
            xs[n] += vx
            ys[n] += vy
        self.heat *= COOLING
