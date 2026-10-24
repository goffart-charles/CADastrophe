"""Exact STEP boundary, used only in a disposable native worker process."""
from pathlib import Path

from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.Interface import Interface_Static
from OCC.Core.STEPControl import STEPControl_Controller, STEPControl_Reader, STEPControl_Writer, STEPControl_AsIs
from OCC.Core.TColStd import TColStd_SequenceOfAsciiString

from cadastrophe.brep_io import decode_shape, encode_shape
from cadastrophe.document import ObjectType

MAX_OBJECTS = 10_000
MAX_SHAPE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
UNITS = {"mm": ("MM", 1.), "cm": ("CM", 10.), "m": ("M", 1000.), "in": ("INCH", 25.4)}


def payload(shape):
    data = encode_shape(shape, ObjectType.BODY)
    if len(data) > MAX_SHAPE:
        raise ValueError("Une forme dépasse la limite de 64 Mio.")
    return data


def read_step(source, directory, progress):
    reader = STEPControl_Reader()
    progress(5, "Lecture du fichier STEP…")
    if reader.ReadFile(str(source)) != IFSelect_RetDone:
        raise ValueError("Lecture STEP impossible : fichier absent, illisible ou incorrect.")
    # Set AFTER ReadFile: the reader must have a model. 1 means millimetres.
    reader.SetSystemLengthUnit(1.)
    lengths, angles, solid_angles = (TColStd_SequenceOfAsciiString() for _ in range(3))
    reader.FileUnits(lengths, angles, solid_angles)
    units = [lengths.Value(i).ToCString() for i in range(1, lengths.Length() + 1)]
    roots = reader.NbRootsForTransfer()
    if not 0 < roots <= MAX_OBJECTS:
        raise ValueError("Le STEP est vide ou dépasse la limite de 10 000 racines.")
    for index in range(1, roots + 1):
        progress(10 + int(65 * (index - 1) / roots), f"Transfert de la racine {index}/{roots}…")
        if not reader.TransferRoot(index):
            raise ValueError(f"Transfert STEP incomplet : racine {index}/{roots} refusée.")
    count = reader.NbShapes()
    if not 0 < count <= MAX_OBJECTS:
        raise ValueError("Aucune géométrie STEP transférable, ou trop de formes.")
    total = 0
    for index in range(1, count + 1):
        data = payload(reader.Shape(index))
        total += len(data)
        if total > MAX_TOTAL:
            raise ValueError("Les géométries dépassent la limite de 256 Mio.")
        (Path(directory) / f"{index - 1}.brep").write_bytes(data)
        progress(75 + int(15 * index / count), f"Validation B-Rep {index}/{count}…")
    return {"count": count, "source_units": units}


def write_step(directory, output, count, unit, progress):
    if unit not in UNITS or type(count) is not int or not 0 < count <= MAX_OBJECTS:
        raise ValueError("Unité ou nombre de corps à exporter invalide.")
    STEPControl_Controller.Init()
    code, factor = UNITS[unit]
    # These process-global options never affect the GUI or another exchange.
    if not Interface_Static.SetCVal("write.step.unit", code):
        raise ValueError("Le moteur STEP refuse l’unité choisie.")
    writer = STEPControl_Writer()
    writer.Model().SetLocalLengthUnit(1.)
    writer.Model().SetWriteLengthUnit(factor)
    total = 0
    for index in range(count):
        path = Path(directory) / f"{index}.brep"
        if path.stat().st_size > MAX_SHAPE:
            raise ValueError("Une forme dépasse la limite de 64 Mio.")
        data = path.read_bytes()
        total += len(data)
        if total > MAX_TOTAL:
            raise ValueError("Les géométries dépassent la limite de 256 Mio.")
        shape = decode_shape(data, ObjectType.BODY)
        progress(20 + int(60 * index / count), f"Transfert du corps {index + 1}/{count}…")
        if writer.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone:
            raise ValueError(f"Le corps {index + 1} ne peut pas être exporté en STEP.")
    progress(85, "Écriture du fichier STEP…")
    if writer.Write(str(output)) != IFSelect_RetDone or not Path(output).stat().st_size:
        raise ValueError("Écriture STEP impossible.")
    return {"count": count, "unit": unit}
