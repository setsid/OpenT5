"""Shaded, textured rendering of meshes on the GPU, for the geometry view.

The wireframe path (``views.mesh``) rasterises with numpy and always works. This
is the shaded path: an OpenGL renderer that draws the mesh with per-material
colour-map textures and a simple lambert light. It is used both for the
interactive view and for offscreen screenshots.

It does not use ``QOpenGLWidget``. A ``QOpenGLWidget`` does not present reliably
under ``QT_QPA_PLATFORM=offscreen`` (its default framebuffer is not captured by
``grab()``), which the screenshots and the self-test depend on. Instead one
offscreen ``QOpenGLContext`` renders every frame into a ``QOpenGLFramebufferObject``
and reads it back as a ``QImage``; the canvas then blits that image with
``QPainter``, exactly as the wireframe path does. The same code therefore runs
interactively (draw once, redraw on camera move) and in a headless screenshot,
and on a real GPU it is far faster than the software rasteriser.

``available()`` says whether a GL context could be created at all; when it
cannot (a machine with no usable GL), the view stays on the wireframe.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import (
    QImage,
    QMatrix4x4,
    QOffscreenSurface,
    QOpenGLContext,
    QSurfaceFormat,
    QVector3D,
)
from shiboken6 import VoidPtr

#: GL enums we need (QOpenGLFunctions does not re-export them as names).
GL_DEPTH_TEST = 0x0B71
GL_TRIANGLES = 0x0004
GL_UNSIGNED_INT = 0x1405
GL_CULL_FACE = 0x0B44
GL_COLOR_BUFFER_BIT = 0x4000
GL_DEPTH_BUFFER_BIT = 0x0100

VERTEX_SRC = """
#version 120
attribute vec3 in_pos;
attribute vec3 in_normal;
attribute vec2 in_uv;
uniform mat4 mvp;
varying vec3 v_normal;
varying vec2 v_uv;
void main() {
    v_normal = in_normal;
    v_uv = in_uv;
    gl_Position = mvp * vec4(in_pos, 1.0);
}
"""

FRAGMENT_SRC = """
#version 120
varying vec3 v_normal;
varying vec2 v_uv;
uniform sampler2D tex;
uniform float use_tex;
uniform vec3 flat_colour;
uniform vec3 light_dir;
void main() {
    vec3 base = flat_colour;
    if (use_tex > 0.5) {
        base = texture2D(tex, v_uv).rgb;
    }
    vec3 n = normalize(v_normal);
    // two-sided: surfaces whose winding faces away still catch the light
    float ndl = abs(dot(n, normalize(light_dir)));
    float lit = 0.35 + 0.65 * ndl;
    gl_FragColor = vec4(base * lit, 1.0);
}
"""


def _face_normals(positions: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Per-vertex normals averaged from the faces, for meshes with no stored normals."""
    n = np.zeros((len(positions), 3), np.float32)
    p = positions
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    fn = np.cross(p[b] - p[a], p[c] - p[a])
    for col in (a, b, c):
        np.add.at(n, col, fn)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    return np.where(ln > 1e-6, n / np.maximum(ln, 1e-6), np.array([0, 0, 1], np.float32)).astype(
        np.float32
    )


class Scene:
    """CPU-side geometry prepared for upload: interleaved vertices, a texture-sorted
    index buffer and the draw runs (texture index, first index, count)."""

    def __init__(self, interleaved: np.ndarray, indices: np.ndarray, runs, textures):
        self.interleaved = interleaved  # (n, 8) float32: pos3, normal3, uv2
        self.indices = indices  # (3m,) uint32, grouped by texture
        self.runs = runs  # list of (tex_index | -1, first, count)
        self.textures = textures  # list of (h, w, 4) uint8 RGBA, or None per slot


def build_scene(
    positions: np.ndarray,
    triangles: np.ndarray,
    normals: np.ndarray | None,
    uvs: np.ndarray | None,
    tri_tex: np.ndarray | None,
    textures: list,
) -> Scene:
    positions = np.ascontiguousarray(positions, np.float32)
    triangles = np.ascontiguousarray(triangles, np.int64).reshape(-1, 3)
    n = len(positions)
    if normals is None or len(normals) != n:
        normals = _face_normals(positions, triangles)
    else:
        normals = np.ascontiguousarray(normals, np.float32)
    if uvs is None or len(uvs) != n:
        uvs = np.zeros((n, 2), np.float32)
    else:
        uvs = np.ascontiguousarray(uvs, np.float32)
    inter = np.empty((n, 8), np.float32)
    inter[:, 0:3] = positions
    inter[:, 3:6] = normals
    inter[:, 6:8] = uvs
    if tri_tex is None or len(tri_tex) != len(triangles):
        tri_tex = np.full(len(triangles), -1, np.int64)
    else:
        tri_tex = np.asarray(tri_tex, np.int64)
    order = np.argsort(tri_tex, kind="stable")
    sorted_tex = tri_tex[order]
    sorted_tris = triangles[order]
    runs = []
    if len(sorted_tex):
        boundaries = np.flatnonzero(np.diff(sorted_tex)) + 1
        starts = np.concatenate([[0], boundaries])
        ends = np.concatenate([boundaries, [len(sorted_tex)]])
        for s, e in zip(starts, ends, strict=True):
            runs.append((int(sorted_tex[s]), int(s * 3), int((e - s) * 3)))
    indices = sorted_tris.reshape(-1).astype(np.uint32)
    return Scene(inter, indices, runs, textures)


class ShadedRenderer:
    """Owns one offscreen GL context and renders a scene into a QImage."""

    def __init__(self):
        self._ctx: QOpenGLContext | None = None
        self._surface: QOffscreenSurface | None = None
        self._failed = False
        self._program = None
        self._vbo = None
        self._ibo = None
        self._gltextures: dict[int, object] = {}
        self._scene: Scene | None = None
        self._loc = {}
        self._fbo = None  # reused across frames, rebuilt only when the size changes
        self._fbo_size = (0, 0)

    def available(self) -> bool:
        return self._ensure_context()

    def _ensure_context(self) -> bool:
        if self._ctx is not None:
            return True
        if self._failed:
            return False
        fmt = QSurfaceFormat()
        fmt.setDepthBufferSize(24)
        fmt.setVersion(2, 1)
        ctx = QOpenGLContext()
        ctx.setFormat(fmt)
        if not ctx.create():
            self._failed = True
            return False
        surface = QOffscreenSurface()
        surface.setFormat(fmt)
        surface.create()
        if not surface.isValid() or not ctx.makeCurrent(surface):
            self._failed = True
            return False
        self._ctx, self._surface = ctx, surface
        return True

    def _make_current(self) -> bool:
        if not self._ensure_context():
            return False
        return self._ctx.makeCurrent(self._surface)

    def _ensure_program(self) -> bool:
        from PySide6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram

        if self._program is not None:
            return True
        prog = QOpenGLShaderProgram()
        stage = QOpenGLShader.ShaderTypeBit
        if not prog.addShaderFromSourceCode(stage.Vertex, VERTEX_SRC):
            self._failed = True
            return False
        if not prog.addShaderFromSourceCode(stage.Fragment, FRAGMENT_SRC):
            self._failed = True
            return False
        prog.bindAttributeLocation("in_pos", 0)
        prog.bindAttributeLocation("in_normal", 1)
        prog.bindAttributeLocation("in_uv", 2)
        if not prog.link():
            self._failed = True
            return False
        prog.bind()
        self._loc = {
            name: prog.uniformLocation(name)
            for name in ("mvp", "use_tex", "flat_colour", "light_dir", "tex")
        }
        prog.release()
        self._program = prog
        return True

    def set_scene(self, scene: Scene) -> bool:
        """Upload a scene's buffers and textures. Returns False if GL is unavailable."""
        from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLTexture

        if not self._make_current() or not self._ensure_program():
            return False
        self._release_buffers()
        self._scene = scene
        self._vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._vbo.create()
        self._vbo.bind()
        flat = np.ascontiguousarray(scene.interleaved, np.float32)
        self._vbo.allocate(flat.tobytes(), flat.nbytes)
        self._vbo.release()
        self._ibo = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)
        self._ibo.create()
        self._ibo.bind()
        idx = np.ascontiguousarray(scene.indices, np.uint32)
        self._ibo.allocate(idx.tobytes(), idx.nbytes)
        self._ibo.release()
        for i, rgba in enumerate(scene.textures):
            if rgba is None:
                continue
            arr = np.ascontiguousarray(rgba, np.uint8)
            h, w = arr.shape[:2]
            image = QImage(arr.tobytes(), w, h, 4 * w, QImage.Format.Format_RGBA8888)
            gltex = QOpenGLTexture(image, QOpenGLTexture.MipMapGeneration.GenerateMipMaps)
            gltex.setMinificationFilter(QOpenGLTexture.Filter.LinearMipMapLinear)
            gltex.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
            gltex.setWrapMode(QOpenGLTexture.WrapMode.Repeat)
            self._gltextures[i] = gltex
        return True

    def render(self, eye, target, up, w: int, h: int, bg, near: float, far: float) -> QImage | None:
        """One frame into a w x h QImage. ``bg`` is (r, g, b) 0..255."""
        from PySide6.QtOpenGL import QOpenGLFramebufferObject

        if self._scene is None or not self._make_current():
            return None
        w, h = max(1, int(w)), max(1, int(h))
        if self._fbo is None or self._fbo_size != (w, h):
            if self._fbo is not None:
                self._fbo.release()
            self._fbo = QOpenGLFramebufferObject(
                w, h, QOpenGLFramebufferObject.Attachment.CombinedDepthStencil
            )
            self._fbo_size = (w, h)
        fbo = self._fbo
        if not fbo.bind():
            return None
        glf = self._ctx.functions()
        glf.glViewport(0, 0, w, h)
        glf.glClearColor(bg[0] / 255.0, bg[1] / 255.0, bg[2] / 255.0, 1.0)
        glf.glEnable(GL_DEPTH_TEST)
        glf.glDisable(GL_CULL_FACE)
        glf.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        proj = QMatrix4x4()
        proj.perspective(50.0, w / h, max(near, 1e-3), far)
        v_eye = QVector3D(float(eye[0]), float(eye[1]), float(eye[2]))
        v_tgt = QVector3D(float(target[0]), float(target[1]), float(target[2]))
        v_up = QVector3D(float(up[0]), float(up[1]), float(up[2]))
        view = QMatrix4x4()
        view.lookAt(v_eye, v_tgt, v_up)
        mvp = proj * view

        prog = self._program
        loc = self._loc
        prog.bind()
        prog.setUniformValue(loc["mvp"], mvp)
        prog.setUniformValue(loc["light_dir"], QVector3D(0.4, 0.3, 0.85))
        self._vbo.bind()
        self._ibo.bind()
        stride = 8 * 4
        prog.enableAttributeArray(0)
        prog.setAttributeBuffer(0, 0x1406, 0, 3, stride)  # GL_FLOAT
        prog.enableAttributeArray(1)
        prog.setAttributeBuffer(1, 0x1406, 3 * 4, 3, stride)
        prog.enableAttributeArray(2)
        prog.setAttributeBuffer(2, 0x1406, 6 * 4, 2, stride)

        flat = QVector3D(0.62, 0.63, 0.66)
        prog.setUniformValue(loc["flat_colour"], flat)
        prog.setUniformValue(loc["tex"], 0)
        for tex_index, first, count in self._scene.runs:
            gltex = self._gltextures.get(tex_index)
            if gltex is not None:
                gltex.bind(0)
                glf.glUniform1f(loc["use_tex"], 1.0)
            else:
                glf.glUniform1f(loc["use_tex"], 0.0)
            glf.glDrawElements(GL_TRIANGLES, count, GL_UNSIGNED_INT, VoidPtr(first * 4))
            if gltex is not None:
                gltex.release()

        prog.disableAttributeArray(0)
        prog.disableAttributeArray(1)
        prog.disableAttributeArray(2)
        self._vbo.release()
        self._ibo.release()
        prog.release()
        glf.glFlush()
        image = fbo.toImage()
        fbo.release()
        return image.convertToFormat(QImage.Format.Format_RGB888)

    def _release_buffers(self) -> None:
        import contextlib

        import shiboken6

        ctx, surface = self._ctx, self._surface
        if (
            ctx is not None
            and shiboken6.isValid(ctx)
            and surface is not None
            and shiboken6.isValid(surface)
        ):
            with contextlib.suppress(RuntimeError):
                ctx.makeCurrent(surface)
        for gltex in self._gltextures.values():
            if shiboken6.isValid(gltex):
                gltex.destroy()
        self._gltextures.clear()
        for buf in (self._vbo, self._ibo):
            if buf is not None and shiboken6.isValid(buf):
                buf.destroy()
        self._vbo = self._ibo = None
        self._scene = None
