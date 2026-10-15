from __future__ import annotations

from src.document.regions import Region


def tile_region(
    region: Region,
    tile_size: float = 0.25,
    overlap: float = 0.05,
) -> list[Region]:
    width = region.x_max - region.x_min
    height = region.y_max - region.y_min

    if width <= tile_size and height <= tile_size:
        return [region]

    tiles: list[Region] = []
    step = tile_size - overlap

    x = region.x_min
    while x < region.x_max:
        y = region.y_min
        while y < region.y_max:
            tile = Region(
                x_min=x,
                y_min=y,
                x_max=min(x + tile_size, region.x_max),
                y_max=min(y + tile_size, region.y_max),
                label=f"tile_{len(tiles)}",
                purpose=region.purpose,
            )
            tiles.append(tile)
            y += step
            if y + tile_size >= region.y_max and tile.y_max < region.y_max:
                y = region.y_max - tile_size
                if y <= tile.y_min:
                    break
        x += step
        if x + tile_size >= region.x_max and tiles[-1].x_max < region.x_max:
            x = region.x_max - tile_size
            if x <= tiles[-1].x_min:
                break

    return tiles
