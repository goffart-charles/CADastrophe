"""Real GPU checks of coplanar region fills on flat and tilted solid faces."""
import pytest
from PySide6.QtGui import QImage

from cadastrophe.presentation import PresentationSurface, ToolInteractionSurface
from test_extrusion_tool import extrusion_system
from test_boss_tool import base_body, face_sketch
from test_extrusion_interaction import orient


@pytest.mark.parametrize("tilted", [False, True])
def test_region_fill_is_uniform_and_distinct_from_its_coplanar_solid(extrusion_system, tmp_path, tilted):
    from OCC.Core.Graphic3d import Graphic3d_TOSM_UNLIT
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    obj = face_sketch(extrusion_system, body)
    frame = obj.sketch.support.frame
    profiles = w.services.service("cadastrophe.sketch.profiles")
    region = profiles.to_region_faces(w.viewport.document, obj.id).regions[0].face
    vp = w.viewport
    orient(w, frame)
    if tilted:
        vp.camera.standard("Isométrique")
        vp.camera.fit()
    surface = ToolInteractionSurface(vp, lambda x, y: True)
    try:
        surface.highlight_regions((region,))
        ais = surface._feedback[0]
        assert ais.Shape().IsSame(region)
        assert ais.Attributes().ShadingAspect().Aspect().ShadingModel() == Graphic3d_TOSM_UNLIT
        vp.view.Redraw()
        path = tmp_path / "coplanar-regions.png"
        assert vp.view.Dump(str(path))
        image = QImage(str(path))
        projection = PresentationSurface(vp)
        colors = []
        for du in (-1., 0., 1.):
            for dv in (-1., 0., 1.):
                x, y = projection.project(frame.to_world(50.+du, 30.+dv))
                colors.append(image.pixelColor(round(x*vp.devicePixelRatioF()), round(y*vp.devicePixelRatioF())))
        assert all(c.blue()-c.red() > 25 for c in colors), [(c.red(), c.green(), c.blue()) for c in colors]
        assert max(c.blue() for c in colors)-min(c.blue() for c in colors) < 8
    finally:
        surface.close()
