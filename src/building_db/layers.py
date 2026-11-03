"""Fixed layers that the DB reads. All other layers are free for people to use.

Layer names are compared after normalize_layer_name() (Jw_cad prefix removed).
"""

GRID = "GRID"
ROOM_BOUNDARY = "ROOM_BOUNDARY"
ROOM_NAME = "ROOM_NAME"
WALL_CENTER = "WALL_CENTER"

FIXED_LAYERS = (GRID, ROOM_BOUNDARY, ROOM_NAME, WALL_CENTER)
