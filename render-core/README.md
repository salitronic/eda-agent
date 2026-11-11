# eda-agent-render

The path-tracing core behind eda-agent's `pcb_render_3d`. Optional: without
it eda-agent renders with its own numpy code, more slowly and with fewer
light bounces.

    pip install eda-agent[render3d]

It takes a scene of triangles, materials and textures, and returns an RGBA
image. It knows nothing about boards; eda-agent builds the scene.

Source: https://github.com/salitronic/eda-agent (the `render-core` folder).
Apache-2.0.
