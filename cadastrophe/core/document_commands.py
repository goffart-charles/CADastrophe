"""Validated reversible mutations. No camera, widget or AIS ownership here."""
from weakref import ref
from PySide6.QtGui import QUndoCommand


class _DocumentCommand(QUndoCommand):
    def __init__(self, document, title):
        super().__init__(title)
        self._document_ref = ref(document)

    @property
    def document(self):
        return self._document_ref()

    def retained_objects(self):
        return ()


class CreateObject(_DocumentCommand):
    def __init__(self, document, obj):
        super().__init__(document, f"Créer {obj.name}")
        self.obj = obj
        self.position = len(document.objects)

    def redo(self):
        self.document._insert(self.obj, self.position)

    def undo(self):
        self.document._remove(self.obj.id)

    def retained_objects(self):
        return (self.obj,)


class RenameObject(_DocumentCommand):
    def __init__(self, document, object_id, name):
        super().__init__(document, f"Renommer {document.objects[object_id].name}")
        self.object_id, self.name = object_id, name
        self.previous = document.objects[object_id].name

    def redo(self):
        self.document._change(self.object_id, name=self.name)

    def undo(self):
        self.document._change(self.object_id, name=self.previous)


class InsertObjects(_DocumentCommand):
    """A validated batch is one undo step; redo reuses the pasted UUIDs."""
    def __init__(self, document, objects, title="Coller les objets"):
        super().__init__(document, title)
        self.objects = tuple(objects)
        self.position = len(document.objects)
        self.previous_selection = document.selected_ids

    def redo(self):
        for offset, obj in enumerate(self.objects):
            self.document._insert(obj, self.position + offset)
        self.document.select_objects(obj.id for obj in self.objects)

    def undo(self):
        for obj in reversed(self.objects):
            self.document._remove(obj.id)
        self.document.select_objects(self.previous_selection)

    def retained_objects(self):
        return self.objects


class SetVisibility(_DocumentCommand):
    def __init__(self, document, changes, title="Modifier la visibilité"):
        super().__init__(document, title)
        self.changes = dict(changes)
        self.previous = {key: document.objects[key].visible for key in changes}

    def redo(self):
        for key, value in self.changes.items():
            self.document._change(key, visible=value)

    def undo(self):
        for key, value in self.previous.items():
            self.document._change(key, visible=value)


class SetTransparency(_DocumentCommand):
    def __init__(self, document, changes):
        super().__init__(document, "Modifier la transparence")
        self.changes = dict(changes)
        self.previous = {key: document.objects[key].transparency for key in changes}

    def redo(self):
        for key, value in self.changes.items():
            self.document._change(key, transparency=value)

    def undo(self):
        for key, value in self.previous.items():
            self.document._change(key, transparency=value)


class SetMaterials(_DocumentCommand):
    def __init__(self, document, changes):
        super().__init__(document, 'Modifier les matériaux')
        self.changes = dict(changes)
        self.previous = {key: document.objects[key].material for key in changes}

    def redo(self):
        for key, value in self.changes.items():
            self.document._change(key, material=value)

    def undo(self):
        for key, value in self.previous.items():
            self.document._change(key, material=value)


class RemoveObjects(_DocumentCommand):
    def __init__(self, document, object_ids):
        super().__init__(document, "Supprimer la sélection")
        self.objects = [(index, obj) for index, obj in enumerate(document.objects.values())
                        if obj.id in object_ids]

    def redo(self):
        for _, obj in self.objects:
            self.document._remove(obj.id)

    def undo(self):
        for position, obj in self.objects:
            self.document._insert(obj, position)

    def retained_objects(self):
        return tuple(obj for _, obj in self.objects)


class CutObjects(RemoveObjects):
    def __init__(self, document, object_ids):
        super().__init__(document, object_ids)
        self.setText("Couper les objets")
        self.previous_selection = document.selected_ids

    def undo(self):
        super().undo()
        self.document.select_objects(self.previous_selection)


class ReplaceShape(_DocumentCommand):
    def __init__(self, document, object_id, shape):
        super().__init__(document, f"Remplacer la forme de {document.objects[object_id].name}")
        self.object_id, self.shape = object_id, shape
        self.previous = document.objects[object_id].shape

    def redo(self):
        self.document._replace_shape(self.object_id, self.shape)

    def undo(self):
        self.document._replace_shape(self.object_id, self.previous)


class ReplaceFeature(_DocumentCommand):
    """Immutable plugin payloads share the document's undo and lifetime rules."""
    def __init__(self, document, previous, current, title):
        super().__init__(document, title)
        self.previous, self.current = previous, current

    def redo(self):
        self.document._replace_feature(self.current)

    def undo(self):
        self.document._replace_feature(self.previous)

    def retained_objects(self):
        return self.previous, self.current


class ClearDocument(_DocumentCommand):
    def __init__(self, document):
        super().__init__(document, "Vider le document")
        self.objects = tuple(document.objects.values())
        self.variables = document.variables
        self.variable_source = document.variable_source
        self.configuration_state = document.configuration_state

    def redo(self):
        self.document._variables = ()
        self.document._variable_source = None
        from .configuration_model import initial_state
        self.document._configuration_state = initial_state(self.document.id)
        self.document._clear()
        self.document.metadata_changed.emit()

    def undo(self):
        self.document._variables = self.variables
        self.document._variable_source = self.variable_source
        self.document._configuration_state = self.configuration_state
        # Publish the complete graph before notifying observers. Inserting
        # a body before its producers can emit shape changes before its tree
        # row exists, and retain native owners in an unhandled Qt traceback.
        self.document._apply_construction(self.objects, {obj.id for obj in self.objects})
        self.document.metadata_changed.emit()

    def retained_objects(self):
        return self.objects


class UpdateConstruction(_DocumentCommand):
    """Publish an already validated graph as one coherent undoable mutation."""
    def __init__(self, document, objects, changed_ids, title, receipts=None, selection=None, variables=None, variable_source=..., configuration_state=None):
        super().__init__(document, title)
        self.previous = tuple(document.objects.values())
        self.current = tuple(objects)
        self.changed_ids = frozenset(changed_ids)
        self.previous_selection = document.selected_ids
        self.current_selection = None if selection is None else tuple(selection)
        self.previous_variables = document.variables
        self.current_variables = document.variables if variables is None else tuple(variables)
        self.previous_source = document.variable_source
        self.current_source = document.variable_source if variable_source is ... else variable_source
        from .configurations import prune
        self.previous_configurations = document.configuration_state
        self.current_configurations = (prune(document.configuration_state, {obj.id: obj for obj in self.current}, self.current_variables)
                                       if configuration_state is None else configuration_state)
        from .configuration_codec import validate_targets
        validate_targets(self.current_configurations, {obj.id: obj for obj in self.current}, self.current_variables)
        from .body_history import affected_bodies, result_receipts
        from .feature_model import ResultStatus
        self.previous_results = result_receipts(document.objects, {
            key: result for key, result in document._body_results.items()
            if receipts and key in affected_bodies(document.objects, changed_ids)},
            {key: states for key, states in document._body_prefixes.items()
             if receipts and key in affected_bodies(document.objects, changed_ids)
             and key in document._body_results})
        self.current_results = receipts or {}

    def redo(self):
        self.document._variables = self.current_variables
        self.document._variable_source = self.current_source
        self.document._configuration_state = self.current_configurations
        self.document._apply_construction(self.current, self.changed_ids, self.current_results)
        self.document.metadata_changed.emit()
        if self.current_selection is not None:
            self.document.select_objects(self.current_selection)

    def undo(self):
        self.document._variables = self.previous_variables
        self.document._variable_source = self.previous_source
        self.document._configuration_state = self.previous_configurations
        self.document._apply_construction(self.previous, self.changed_ids, self.previous_results)
        self.document.metadata_changed.emit()
        if self.current_selection is not None:
            self.document.select_objects(self.previous_selection)

    def retained_objects(self):
        return self.previous + self.current
