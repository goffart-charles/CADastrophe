"""Arithmetic security, stable bindings and real native parametric rebuilds."""
from dataclasses import replace
from uuid import uuid4

import pytest

from cadastrophe.core.expressions import Expression, ExpressionError, Node, Variable, bind, evaluate
from cadastrophe.core.parameters import graph
from cadastrophe.core.persistence import load_document, save_document
from test_sketch_document import setup_document, registry
from plugins.sketch.commands import add_entities, set_constraints
from plugins.sketch.constraints import Constraint, Reference
from plugins.sketch.model import Circle, Vec2


@pytest.mark.parametrize("text,expected", [("=15*3", 45), ("=100/2+5", 55),
                                          ("=-(2.5-5)*4", 10), ("=(10+2)/3", 4)])
def test_equations_arithmetic(text, expected):
    assert evaluate((Node("a", "a", 0, "length", Expression("a", text)),))["a"] == expected


@pytest.mark.parametrize("text,match", [("=x+1", "inconnue"), ("=1/0", "zéro"),
                                       ("=2+", "Syntaxe"), ("=__import__('os')", "Seuls"),
                                       ("=2**3", "Seuls"), ("=1e999", "fini"), ("=True", "Seuls")])
def test_equations_invalid_input(text, match):
    with pytest.raises(ExpressionError, match=match):
        exp = bind(Expression("a", text), {})
        evaluate((Node("a", "a", 0, "length", exp),))


def test_equations_cycle_and_unit_errors():
    a = Node("a", "largeur", 0, "length", Expression("a", "=b", (("b", "b"),)))
    b = Node("b", "hauteur", 0, "length", Expression("b", "=a", (("a", "a"),)))
    with pytest.raises(ExpressionError, match="circulaire"):
        evaluate((a, b))
    with pytest.raises(ExpressionError, match="Unité"):
        evaluate((Node("angle", "angle", 30, "angle"),
                  Node("a", "a", 0, "length", Expression("a", "=angle", (("angle", "angle"),)))))


def variable(name, text):
    return Variable(name, Expression("value", text if text.startswith("=") else "="+text))


def sketch_document():
    doc, obj = setup_document(native=True)
    circles = (Circle(Vec2(0, 0), 5), Circle(Vec2(30, 0), 5))
    add_entities(doc, obj.id, circles, title="Cercles")
    constraints = tuple(Constraint("radius", (Reference(c.id),), 5.) for c in circles)
    set_constraints(doc, obj.id, constraints)
    return doc, obj.id, constraints


def test_equations_sketch_dependencies_atomic_undo_reload(registry, tmp_path):
    doc, key, constraints = sketch_document()
    doc.set_variables((variable("largeur", "100"), variable("hauteur", "=largeur/2")))
    for c in constraints:
        doc.set_equation(key, c.id, "=hauteur/2")
    assert [c.radius for c in doc.objects[key].sketch.entities] == pytest.approx([25, 25])
    original = doc.objects[key]
    doc.history.clear()
    values = tuple(replace(v, expression=Expression("value", "=200")) if v.name == "largeur" else v for v in doc.variables)
    doc.set_variables(values)
    assert [c.radius for c in doc.objects[key].sketch.entities] == pytest.approx([50, 50])
    assert doc.history.count == 1
    doc.history.undo()
    assert doc.objects[key].sketch == original.sketch
    doc.history.redo()
    path = tmp_path / "equations.cada"
    save_document(doc, path, registry=registry)
    loaded = load_document(path, registry=registry)
    assert loaded.variables == doc.variables
    assert loaded.objects[key].expressions == doc.objects[key].expressions
    loaded.set_variables(tuple(replace(v, expression=Expression("value", "=80")) if v.name == "largeur" else v for v in loaded.variables))
    assert [c.radius for c in loaded.objects[key].sketch.entities] == pytest.approx([20, 20])
    snapshot = dict(loaded.objects)
    with pytest.raises(ValueError, match="absente"):
        loaded.set_variables(())
    assert dict(loaded.objects) == snapshot
    with pytest.raises(ValueError, match="circulaire"):
        loaded.set_variables(tuple(replace(v, expression=Expression("value", "=hauteur")) if v.name == "largeur" else v for v in loaded.variables))
    assert dict(loaded.objects) == snapshot


def test_equations_dimension_refs_keep_uuid_after_rename_and_reordering():
    doc, key, constraints = sketch_document()
    doc.set_equation(key, constraints[0].id, "=15*3")
    name = doc.objects[key].name
    doc.set_equation(key, constraints[1].id, f"=[D1@{name}]/3")
    doc.rename(key, "Profil renommé")
    doc.set_equation(key, constraints[0].id, "=60")
    assert doc.objects[key].sketch.entities[1].radius == pytest.approx(20)
    set_constraints(doc, key, tuple(reversed(doc.objects[key].sketch.constraints)))
    doc.set_equation(key, constraints[0].id, "=90")
    assert doc.objects[key].sketch.entities[1].radius == pytest.approx(30)
    before = dict(doc.objects)
    with pytest.raises(ValueError, match="absente"):
        set_constraints(doc, key, tuple(c for c in doc.objects[key].sketch.constraints if c.id == constraints[1].id))
    assert dict(doc.objects) == before


def test_equations_length_editor_preserves_expression_and_units():
    from PySide6.QtWidgets import QApplication
    from cadastrophe.ui.length_input import LengthSpinBox
    from cadastrophe.core.units import DisplayUnits
    app = QApplication.instance() or QApplication([])
    doc, key, constraints = sketch_document()
    doc.set_variables((variable("largeur", "100"),))
    field = LengthSpinBox(DisplayUnits(), 0., 10000., 5.)
    field.bind_equations(doc, constraints[0].id, owner=key)
    field.lineEdit().setText("=largeur/2")
    assert field.hasAcceptableInput()
    field.interpretText()
    assert field.internal_value == 50
    assert field.equation_expression().text == "=largeur/2"
    field.lineEdit().setText("=lar mm")
    field._insert_variable("largeur — 100 mm")
    field.interpretText()
    assert field.equation_expression().text == "=largeur" and field.internal_value == 100
    doc.set_variables((replace(doc.variables[0], expression=Expression("value", "=200")),))
    field._equation_edited("=lar")
    assert "largeur — 200 mm" in field._completer.model().stringList()
    field.deleteLater()


def test_equations_raw_entity_parameters_are_bound_and_constraint_conflicts_rejected():
    from plugins.sketch.commands import replace_entities
    doc, obj = setup_document(native=True)
    circle = Circle(Vec2(0, 0), 5.)
    add_entities(doc, obj.id, (circle,), title="Cercle")
    doc.set_variables((variable("rayon", "12"),))
    doc.set_equation(obj.id, circle.id+".radius", "=rayon")
    assert doc.objects[obj.id].sketch.entities[0].radius == 12
    snapshot = dict(doc.objects)
    c = Constraint("radius", (Reference(circle.id),), 20.)
    with pytest.raises(ValueError, match="incompatible"):
        set_constraints(doc, obj.id, (c,))
    assert dict(doc.objects) == snapshot


def test_equations_variable_rename_and_name_reuse_preserve_existing_binding():
    doc, key, constraints = sketch_document()
    original = variable("largeur", "20")
    doc.set_variables((original,))
    doc.set_equation(key, constraints[0].id, "=largeur")
    renamed = replace(doc.variables[0], name="nouvelle_largeur")
    doc.set_variables((renamed, variable("largeur", "99")))
    assert doc.objects[key].sketch.entities[0].radius == 20
    doc.set_variables((replace(renamed, expression=Expression("value", "=30")), doc.variables[1]))
    assert doc.objects[key].sketch.entities[0].radius == 30
    from PySide6.QtWidgets import QApplication
    from cadastrophe.ui.length_input import LengthSpinBox
    from cadastrophe.core.units import DisplayUnits
    app = QApplication.instance() or QApplication([])
    field = LengthSpinBox(DisplayUnits(), 0., 1000., 30.)
    field.bind_equations(doc, constraints[0].id, owner=key)
    field._insert_variable("largeur")
    field.interpretText()
    from cadastrophe.core.parameters import set_expression
    doc.update_construction((set_expression(doc.objects[key], constraints[0].id, field.equation_expression()),))
    assert doc.objects[key].sketch.entities[0].radius == 99
    field.deleteLater()


def test_equations_display_units_and_integer_validation():
    from PySide6.QtWidgets import QApplication
    from cadastrophe.ui.length_input import LengthSpinBox
    from cadastrophe.plugin_api import EquationIntSpinBox
    from cadastrophe.core.units import DisplayUnits
    app = QApplication.instance() or QApplication([])
    doc, key, constraints = sketch_document()
    field = LengthSpinBox(DisplayUnits("cm"), 0., 10000., 5.)
    field.bind_equations(doc, constraints[0].id, owner=key, scale=10.)
    field.lineEdit().setText("=15*3")
    field.interpretText()
    assert field.internal_value == 450
    assert field.equation_expression().scale == 10
    count = EquationIntSpinBox()
    count.setRange(2, 256)
    count.bind_equations(doc, "count", unit="number")
    count.lineEdit().setText("=15/2")
    assert not count.hasAcceptableInput()
    with pytest.raises(ValueError, match="entier"):
        count.interpretText()
    count.lineEdit().setText("=3*2")
    count.interpretText()
    assert count.value() == 6
    count.deleteLater()
    field.deleteLater()


def test_equations_numeric_current_schema_and_corrupt_bound_reference(registry, tmp_path):
    import json
    from zipfile import ZipFile, ZIP_DEFLATED
    from cadastrophe.core.persistence import DocumentFileError
    doc, key, constraints = sketch_document()
    path = tmp_path / "numeric.cada"
    save_document(doc, path, registry=registry)
    with ZipFile(path) as archive:
        assert json.loads(archive.read("manifest.json"))["schema_version"] == 9
    loaded = load_document(path, registry=registry)
    assert loaded.variables == () and loaded.objects[key].expressions == ()
    doc.set_variables((variable("rayon", "12"),))
    doc.set_equation(key, constraints[0].id, "=rayon")
    save_document(doc, path, registry=registry)
    with ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    metadata = json.loads(entries["manifest.json"])
    record = next(o for o in metadata["objects"] if o["id"] == key)
    record["expressions"][0]["bindings"][0][1] = str(uuid4())
    entries["manifest.json"] = json.dumps(metadata).encode()
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    with pytest.raises(DocumentFileError, match="absente"):
        load_document(path, registry=registry)


def test_equations_variables_without_features_roundtrip(tmp_path):
    from cadastrophe.scene import new_document
    doc = new_document()
    doc.set_variables((variable("largeur", "100"), variable("hauteur", "=largeur/2")))
    path = tmp_path/"variables-only.cada"
    save_document(doc, path)
    loaded = load_document(path)
    assert loaded.variables == doc.variables
    assert len(loaded.objects) == len(doc.objects)
    assert evaluate(graph(loaded.objects, loaded.variables)[0])[loaded.variables[1].id] == 50


def test_equations_datum_numeric_source_edits_propagate():
    from test_parametric_operations import system, datum, plane
    from cadastrophe.core.operation_model import PlaneParameters, GeometryReference as Ref
    from cadastrophe.core.parameters import prepare
    doc, service = system()
    source = datum(doc, PlaneParameters("offset", (Ref("plane", plane(doc).id),), distance=5.))
    doc.set_variables((variable("distance", f"=[D1@{source.name}]*2"),))
    doc.replace_feature(replace(doc.objects[source.id], parameters=replace(source.parameters, distance=12.)))
    _, _, values = prepare(doc.objects, doc.variables)
    assert values[doc.variables[0].id] == 24


def test_equations_sketch_reference_updates_derived_plane_atomically():
    from test_parametric_operations import system, datum, plane, profile
    from cadastrophe.core.operation_model import PlaneParameters, GeometryReference as Ref
    doc, service = system()
    source = profile(doc)
    edge = source.sketch.entities[0]
    support = datum(doc, PlaneParameters("inclined", (Ref("plane", plane(doc).id),
                    Ref("sketch_line", source.id, entity_id=edge.id)), angle=30.))
    doc.equation_evaluator = service.evaluator
    doc.set_variables((variable("hauteur", "0"),))
    doc.set_equation(source.id, edge.id+".bv", "=hauteur")
    initial = doc.objects[support.id].plane
    doc.set_variables((replace(doc.variables[0], expression=Expression("value", "=10")),))
    assert doc.objects[support.id].plane.normal != initial.normal
    doc.history.undo()
    assert doc.objects[support.id].plane == initial


@pytest.mark.parametrize("folder,key,unit", [
    ("conge", "size", "length"), ("chanfrein", "size", "length"),
    ("coque", "thickness", "length"), ("depouille", "angle", "angle"),
    ("plan-reference/decale", "distance", "length"),
    ("repetition-lineaire", "count", "number"),
    ("repetition-circulaire", "count", "number"),
    ("balayage/droit", "roll", "angle"),
])
def test_equations_existing_native_recipe_families(folder, key, unit, tmp_path):
    from pathlib import Path
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from examples.parametric_example import registry as native_registry, mass
    from plugins.modeling.extrusion_service import Extrusions
    from plugins.sketch.profile_service import SketchProfiles
    from cadastrophe.core.feature_objects import BodyObject
    from cadastrophe.core.parameters import dimensions
    service = Extrusions(SketchProfiles())
    codecs = native_registry(service)
    doc = load_document(Path(__file__).parent/"fonctions"/folder/"02-apres.cada", registry=codecs)
    doc.equation_evaluator = service.evaluator
    target = next(d for d in dimensions(doc.objects) if d.key == key)
    initial = target.value
    doc.set_variables((replace(variable("pilote", f"={initial}"), unit=unit),))
    doc.set_equation(target.owner, key, "=pilote")
    before = {o.id: mass(o.shape) for o in doc.objects.values() if isinstance(o, BodyObject)}
    next_value = 2 if key == "count" else 25 if key == "roll" else initial*.75
    assert next_value != initial
    doc.set_variables((replace(doc.variables[0], expression=Expression("value", f"={next_value}")),))
    assert getattr(doc.objects[target.owner].parameters, key) == pytest.approx(next_value)
    bodies = [o for o in doc.objects.values() if isinstance(o, BodyObject)]
    if key == "distance":
        assert doc.objects[target.owner].plane.origin.z == pytest.approx(next_value)
        assert BRepCheck_Analyzer(doc.objects[target.owner].shape).IsValid()
    else:
        assert bodies and all(o.shape is not None and BRepCheck_Analyzer(o.shape).IsValid() for o in bodies)
    if key not in ("distance", "roll"):
        assert any(abs(mass(o.shape)-before[o.id]) > 1e-6 for o in bodies)
    doc.history.undo()
    assert getattr(doc.objects[target.owner].parameters, key) == pytest.approx(initial)
    assert all(mass(doc.objects[k].shape) == pytest.approx(v) for k, v in before.items())
    doc.history.redo()
    path = tmp_path/"equation-recipe.cada"
    save_document(doc, path, registry=codecs)
    loaded = load_document(path, registry=codecs)
    assert loaded.variables == doc.variables
    assert loaded.objects[target.owner].expressions == doc.objects[target.owner].expressions
    assert getattr(loaded.objects[target.owner].parameters, key) == pytest.approx(next_value)
    if folder == "conge":
        snapshot, variables, count = dict(doc.objects), doc.variables, doc.history.count
        with pytest.raises(ValueError):
            doc.set_variables((replace(doc.variables[0], expression=Expression("value", "=1000")),))
        assert dict(doc.objects) == snapshot and doc.variables == variables and doc.history.count == count
