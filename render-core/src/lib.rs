// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
//! eda-agent's path tracer. Python builds the scene (triangles,
//! materials, textures, camera, lights) and gets an RGBA image back.

pub mod bvh;
pub mod math;
pub mod scene;
pub mod trace;

use math::V3;
use scene::{Material, Scene, Texture};

/// What a scene is made of, as flat little-endian arrays.
pub struct Input<'a> {
    pub positions: &'a [u8],
    pub normals: Option<&'a [u8]>,
    pub uvs: Option<&'a [u8]>,
    pub indices: &'a [u8],
    pub material_ids: &'a [u8],
}

fn f32s(b: &[u8]) -> Vec<f32> {
    b.chunks_exact(4).map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect()
}

fn u32s(b: &[u8]) -> Vec<u32> {
    b.chunks_exact(4).map(|c| u32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect()
}

fn v3s(b: &[u8]) -> Vec<V3> {
    f32s(b).chunks_exact(3).map(|c| V3::new(c[0], c[1], c[2])).collect()
}

/// Check the arrays agree and build the scene; Err names what is wrong.
pub fn build_scene(
    inp: &Input,
    materials: Vec<Material>,
    textures: Vec<Texture>,
) -> Result<Scene, String> {
    let pos = v3s(inp.positions);
    let n = pos.len();
    let idx: Vec<[u32; 3]> = u32s(inp.indices).chunks_exact(3).map(|c| [c[0], c[1], c[2]]).collect();
    let mat = u32s(inp.material_ids);
    if mat.len() != idx.len() {
        return Err(format!("{} material ids for {} triangles", mat.len(), idx.len()));
    }
    if let Some(bad) = idx.iter().flatten().find(|&&i| i as usize >= n) {
        return Err(format!("index {bad} past the {n} vertices"));
    }
    if let Some(bad) = mat.iter().find(|&&m| m as usize >= materials.len()) {
        return Err(format!("material {bad} past the {} materials", materials.len()));
    }
    for (i, m) in materials.iter().enumerate() {
        if m.texture >= 0 && m.texture as usize >= textures.len() {
            return Err(format!("material {i} names texture {} of {}", m.texture, textures.len()));
        }
    }
    for (i, t) in textures.iter().enumerate() {
        if t.w == 0 || t.h == 0 || t.rgba.len() != t.w * t.h * 4 {
            return Err(format!("texture {i}: {} bytes for {}x{}", t.rgba.len(), t.w, t.h));
        }
        if let Some(p) = &t.params {
            if p.len() != t.rgba.len() {
                return Err(format!("texture {i}: parameter map size differs"));
            }
        }
    }
    let nrm = match inp.normals {
        Some(b) => {
            let v = v3s(b);
            if v.len() != n {
                return Err(format!("{} normals for {n} vertices", v.len()));
            }
            Some(v)
        }
        None => None,
    };
    let uv = match inp.uvs {
        Some(b) => {
            let v: Vec<[f32; 2]> = f32s(b).chunks_exact(2).map(|c| [c[0], c[1]]).collect();
            if v.len() != n {
                return Err(format!("{} uvs for {n} vertices", v.len()));
            }
            Some(v)
        }
        None => None,
    };
    Ok(Scene::new(pos, nrm, uv, idx, mat, materials, textures))
}

#[cfg(feature = "python")]
mod py {
    use super::*;
    use pyo3::exceptions::PyValueError;
    use pyo3::prelude::*;
    use pyo3::types::{PyBytes, PyDict};
    use crate::trace::{self, Camera, Light, Settings};

    fn get<'py, T: FromPyObject<'py>>(d: &Bound<'py, PyDict>, k: &str, default: T) -> PyResult<T> {
        match d.get_item(k)? {
            Some(v) if !v.is_none() => v.extract(),
            _ => Ok(default),
        }
    }

    fn v3(t: (f32, f32, f32)) -> V3 {
        V3::new(t.0, t.1, t.2)
    }

    /// Render a scene.
    ///
    /// materials: list of dicts (base, roughness, metallic, emission,
    /// texture, coat). textures: list of (width, height, rgba bytes,
    /// params bytes or None). camera and settings: dicts. Returns
    /// (rgba bytes, width, height), rows top to bottom.
    #[pyfunction]
    #[pyo3(signature = (positions, indices, material_ids, materials, textures, camera, settings, normals=None, uvs=None))]
    #[allow(clippy::too_many_arguments)]
    fn render<'py>(
        py: Python<'py>,
        positions: &[u8],
        indices: &[u8],
        material_ids: &[u8],
        materials: Vec<Bound<'py, PyDict>>,
        textures: Vec<(usize, usize, Vec<u8>, Option<Vec<u8>>)>,
        camera: Bound<'py, PyDict>,
        settings: Bound<'py, PyDict>,
        normals: Option<&[u8]>,
        uvs: Option<&[u8]>,
    ) -> PyResult<(Py<PyBytes>, usize, usize)> {
        let mats = materials
            .iter()
            .map(|m| {
                Ok(Material {
                    base: v3(get(m, "base", (0.8, 0.8, 0.8))?),
                    roughness: get(m, "roughness", 0.5f32)?,
                    metallic: get(m, "metallic", 0.0f32)?,
                    emission: v3(get(m, "emission", (0.0, 0.0, 0.0))?),
                    texture: get(m, "texture", -1i32)?,
                    coat: get(m, "coat", 0.0f32)?,
                    bump: get(m, "bump", 0.0f32)?,
                    transmission: get(m, "transmission", 0.0f32)?,
                })
            })
            .collect::<PyResult<Vec<_>>>()?;
        let texs = textures
            .into_iter()
            .map(|(w, h, rgba, params)| Texture { w, h, rgba, params })
            .collect();
        let cam = Camera {
            pos: v3(get(&camera, "position", (0.0, 0.0, 10.0))?),
            target: v3(get(&camera, "target", (0.0, 0.0, 0.0))?),
            up: v3(get(&camera, "up", (0.0, 1.0, 0.0))?),
            fov_deg: get(&camera, "fov_deg", 30.0f32)?,
            ortho_height: get(&camera, "ortho_height", 0.0f32)?,
            aperture: get(&camera, "aperture", 0.0f32)?,
            focus: get(&camera, "focus", 0.0f32)?,
        };
        let lights: Vec<(Vec<f32>, Vec<f32>, f32)> = get(&settings, "lights", Vec::new())?;
        let st = Settings {
            width: get(&settings, "width", 1600usize)?,
            height: get(&settings, "height", 900usize)?,
            spp: get(&settings, "spp", 64u32)?,
            max_bounces: get(&settings, "max_bounces", 4u32)?,
            exposure: get(&settings, "exposure", 1.0f32)?,
            background: get(&settings, "background", 0u32)?,
            background_color: v3(get(&settings, "background_color", (1.0, 1.0, 1.0))?),
            sky_zenith: v3(get(&settings, "sky_zenith", (0.9, 0.93, 1.0))?),
            sky_horizon: v3(get(&settings, "sky_horizon", (1.0, 1.0, 1.0))?),
            sky_ground: v3(get(&settings, "sky_ground", (0.35, 0.33, 0.3))?),
            lights: lights
                .into_iter()
                .filter(|(d, r, _)| d.len() == 3 && r.len() == 3)
                .map(|(d, r, a)| Light {
                    dir: V3::new(d[0], d[1], d[2]).norm(),
                    radiance: V3::new(r[0], r[1], r[2]),
                    angle_deg: a,
                })
                .collect(),
            seed: get(&settings, "seed", 1u64)?,
            denoise: get(&settings, "denoise", false)?,
        };
        if st.width == 0 || st.height == 0 || st.width * st.height > 64_000_000 {
            return Err(PyValueError::new_err("image size out of range"));
        }
        let inp = Input { positions, normals, uvs, indices, material_ids };
        let scene = build_scene(&inp, mats, texs).map_err(PyValueError::new_err)?;
        let img = py.allow_threads(|| trace::render(&scene, &cam, &st));
        Ok((PyBytes::new(py, &img).unbind(), st.width, st.height))
    }

    #[pymodule]
    fn eda_agent_render(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add("__version__", env!("CARGO_PKG_VERSION"))?;
        m.add_function(wrap_pyfunction!(render, m)?)?;
        Ok(())
    }
}
