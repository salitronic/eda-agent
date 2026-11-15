# eda-agent-route

The grid path search behind eda-agent's board router (`pcb_autoroute`).
Optional: without it eda-agent searches with scipy, more slowly, and finds
paths of the same cost.

    pip install eda-agent[route]

It takes a region of the routing grid, the cost of each cell and via, and
the start and end cells, and returns the cheapest path. It knows nothing
about boards; eda-agent builds the costs.

Source: https://github.com/salitronic/eda-agent (the `route-core` folder).
Apache-2.0.
