"""Shared text variable libraries; explicit reload, stable IDs and atomic writes."""
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID, uuid4

from .expressions import Variable, ExpressionError, expression_record, read_expression

MAX_BYTES = 4*1024*1024
MAX_VARIABLES = 4096


def uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError('Identifiant de variable partagée invalide.')


@dataclass(frozen=True)
class VariableSource:
    path: str
    library_id: str
    digest: str
    bindings: tuple[tuple[str, str], ...]

    def __post_init__(self):
        if type(self.path) is not str or not self.path.strip() or '\0' in self.path or len(self.path) > 32767:
            raise ValueError('Chemin du fichier de variables invalide.')
        uuid(self.library_id)
        if type(self.digest) is not str or len(self.digest) != 64 or any(c not in '0123456789abcdef' for c in self.digest):
            raise ValueError('Empreinte du fichier de variables invalide.')
        if type(self.bindings) is not tuple or len(self.bindings) > MAX_VARIABLES:
            raise ValueError('Correspondances de variables invalides.')
        for pair in self.bindings:
            if type(pair) is not tuple or len(pair) != 2:
                raise ValueError('Correspondance de variable invalide.')
            for value in pair:
                uuid(value)
        if any(len({pair[i] for pair in self.bindings}) != len(self.bindings) for i in (0, 1)):
            raise ValueError('Correspondance de variable dupliquée.')


@dataclass(frozen=True)
class VariableLibrary:
    id: str
    variables: tuple[Variable, ...]

    def __post_init__(self):
        uuid(self.id)
        if type(self.variables) is not tuple or len(self.variables) > MAX_VARIABLES:
            raise ValueError('Liste de variables partagées invalide ou trop grande.')
        ids = {v.id for v in self.variables}
        if any(target not in ids for v in self.variables for _, target in v.expression.bindings):
            raise ExpressionError('Une variable partagée ne peut pas dépendre d’une cote propre à un projet.')
        from .parameters import graph
        from .expressions import evaluate
        nodes, bound, _, _ = graph({}, self.variables)
        evaluate(nodes)
        object.__setattr__(self, 'variables', bound)


def source_record(source, directory):
    if source is None:
        return None
    try:
        path = os.path.relpath(source.path, directory)
    except ValueError:  # another Windows drive
        path = source.path
    return dict(path=path, library_id=source.library_id, digest=source.digest,
                bindings=[list(pair) for pair in source.bindings])


def read_source(data, directory):
    if data is None:
        return None
    if type(data) is not dict or set(data) != {'path', 'library_id', 'digest', 'bindings'} or type(data['bindings']) is not list:
        raise ValueError('Lien vers le fichier de variables invalide.')
    if any(type(pair) is not list for pair in data['bindings']):
        raise ValueError('Correspondance de variable invalide.')
    # Validate text before passing it to the filesystem path parser.
    value = VariableSource(**dict(data, bindings=tuple(tuple(pair) for pair in data['bindings'])))
    return replace(value, path=os.path.abspath(os.path.join(directory, value.path)))


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('Clé dupliquée dans le fichier de variables : '+key)
        out[key] = value
    return out


def read_library(path):
    try:
        with Path(path).open('rb') as stream:
            payload = stream.read(MAX_BYTES+1)
        if len(payload) > MAX_BYTES:
            raise ValueError('Le fichier de variables dépasse 4 Mo.')
        data = json.loads(payload.decode('utf-8-sig'), object_pairs_hook=_unique,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nombre non fini : '+value)))
        if (type(data) is not dict or set(data) != {'format', 'schema_version', 'id', 'length_unit', 'angle_unit', 'variables'}
                or data['format'] != 'cadastrophe.variables' or type(data['schema_version']) is not int
                or data['schema_version'] != 1 or data['length_unit'] != 'mm' or data['angle_unit'] != 'deg'
                or type(data['variables']) is not list or len(data['variables']) > MAX_VARIABLES):
            raise ValueError('Format de fichier de variables inconnu ou invalide.')
        variables = []
        for v in data['variables']:
            if type(v) is not dict or set(v) != {'id', 'name', 'unit', 'expression'}:
                raise ValueError('Enregistrement de variable invalide.')
            variables.append(Variable(v['name'], read_expression(v['expression']), v['id'], v['unit']))
        return VariableLibrary(data['id'], tuple(variables)), sha256(payload).hexdigest()
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, RecursionError) as error:
        raise ValueError(f'Lecture du fichier de variables impossible : {error}') from error


def bind_library(document, path, *, reload=False):
    """Read and validate the entire candidate before publishing any project state."""
    document.history._check_idle()
    path = Path(path).resolve()
    library, digest = read_library(path)
    previous = document.variable_source
    if reload and (previous is None or library.id != previous.library_id):
        raise ValueError('Ce fichier n’est plus la bibliothèque liée ; choisissez explicitement le nouveau fichier.')
    mapped = dict(previous.bindings) if previous and previous.library_id == library.id else {}
    local = {v.id: v for v in document.variables}
    by_name = {v.name: v.id for v in document.variables}
    mapping = {}
    for variable in library.variables:
        mapping[variable.id] = mapped.get(variable.id, variable.id if variable.id in local else by_name.get(variable.name, variable.id))
    if len(set(mapping.values())) != len(mapping):
        raise ValueError('Deux variables partagées désignent la même variable locale ; renommez le conflit avant de lier.')
    imported = tuple(replace(v, id=mapping[v.id], expression=replace(v.expression,
                     bindings=tuple((name, mapping[target]) for name, target in v.expression.bindings))) for v in library.variables)
    replaced = set(mapping.values()) | (set(mapped.values()) if reload else set())
    variables = tuple(v for v in document.variables if v.id not in replaced)+imported
    source = VariableSource(str(path), library.id, digest, tuple(mapping.items()))
    document.update_construction(variables=variables, variable_source=source,
                                 title='Recharger les variables partagées' if reload else 'Lier un fichier de variables')
    return source


def reload_variables(document):
    if document.variable_source is None:
        raise ValueError('Aucun fichier de variables n’est lié à ce projet.')
    return bind_library(document, document.variable_source.path, reload=True)


def export_variables(document, path):
    """Export and link; publishing to an already linked file detects other writers."""
    from PySide6.QtCore import QLockFile
    document.history._check_idle()
    path = Path(path).resolve()
    previous = document.variable_source
    same = previous is not None and Path(previous.path) == path
    # Keep library IDs on publication, even when this project uses other local UUIDs.
    reverse = {local: shared for shared, local in previous.bindings} if same else {}
    mapping = {v.id: reverse.get(v.id, v.id) for v in document.variables}
    variables = tuple(replace(v, id=mapping[v.id], expression=replace(v.expression,
                      bindings=tuple((name, mapping.get(target, target)) for name, target in v.expression.bindings))) for v in document.variables)
    library = VariableLibrary(previous.library_id if same else str(uuid4()), variables)
    from .parameters import prepare
    prepare(document.objects, document.variables)
    data = dict(format='cadastrophe.variables', schema_version=1, id=library.id, length_unit='mm', angle_unit='deg',
                variables=[dict(id=v.id, name=v.name, unit=v.unit, expression=expression_record(v.expression)) for v in library.variables])
    payload = (json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2)+'\n').encode('utf8')
    if len(payload) > MAX_BYTES:
        raise ValueError('Le fichier de variables dépasse 4 Mo.')
    source = VariableSource(str(path), library.id, sha256(payload).hexdigest(), tuple((shared, local) for local, shared in mapping.items()))
    lock = QLockFile(str(path)+'.lock')
    if not lock.tryLock(0):
        raise ValueError('Le fichier de variables est utilisé par une autre opération.')
    temporary = None
    try:
        if same and path.exists():
            with path.open('rb') as stream:
                current = stream.read(MAX_BYTES+1)
            if sha256(current).hexdigest() != previous.digest:
                raise ValueError('Le fichier partagé a été modifié ailleurs. Rechargez-le avant de l’écraser.')
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.'+path.name+'.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        document.update_construction(variable_source=source, title='Enregistrer et lier les variables')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlock()
    return source
