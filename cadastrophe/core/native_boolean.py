"""Build OCCT booleans without modifying shared prefix or preview inputs."""


def build_boolean(kind, left, right):
    from OCC.Core.TopTools import TopTools_ListOfShape
    arguments, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    arguments.Append(left)
    tools.Append(right)
    builder = kind()
    builder.SetArguments(arguments)
    builder.SetTools(tools)
    # The two-shape constructor builds immediately, before this can be set.
    builder.SetNonDestructive(True)
    builder.Build()
    return builder
