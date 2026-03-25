#!/usr/bin/env python3
"""
Download OSM map tiles for the DQRMAN demo area.

This script should be run once while internet is available to cache tiles for offline operation.
It downloads tiles for zoom levels 13, 14, and 15 in a 5x5 grid around the configured coordinates.
Tiles are stored in Flask's static directory for serving via the /osm-tiles endpoint proxy.
"""

import os
import math
import time
import urllib.request
import urllib.error

# Demo area coordinates (change these to your demo city)
CENTER_LAT = 28.6139  # New Delhi latitude
CENTER_LON = 77.2090  # New Delhi longitude

# Tile cache directory (relative to project root)
TILE_CACHE_DIR = 'backend/static/osm-tiles'

# Zoom levels to cache
ZOOM_LEVELS = [13, 14, 15]

# Grid size around center point for each zoom level
GRID_SIZE = 5  # Will fetch a 5x5 grid of tiles

# Request delay (milliseconds) to respect OSM tile server
REQUEST_DELAY_MS = 50

# User-Agent header for tile requests
USER_AGENT = 'DQRMAN-Mesh-Dashboard/1.0 (+https://github.com/dqrman)'


def lat_lon_to_tile(lat, lon, zoom):
    """
    Convert latitude and longitude to tile coordinates (x, y) at given zoom level.
    Uses Web Mercator projection standard formula.
    
    Args:
        lat: Latitude in degrees (-85 to 85)
        lon: Longitude in degrees (-180 to 180)
        zoom: Zoom level (0-28)
    
    Returns:
        Tuple of (x, y) tile coordinates
    """
    n = 2 ** zoom
    lat_rad = math.radians(lat)
    
    x = (lon + 180) / 360 * n
    y = (1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2 * n
    
    return int(x), int(y)


def download_tile(x, y, z):
    """
    Download a single OSM tile and cache it locally.
    
    Args:
        x: Tile X coordinate
        y: Tile Y coordinate
        z: Zoom level
    
    Returns:
        True if tile was downloaded or already exists, False on error
    """
    tile_path = os.path.join(TILE_CACHE_DIR, str(z), str(x))
    os.makedirs(tile_path, exist_ok=True)
    
    tile_file = os.path.join(tile_path, f'{y}.png')
    
    # Skip if tile already cached
    if os.path.exists(tile_file):
        return False
    
    url = f'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
    
    try:
        request = urllib.request.Request(
            url,
            headers={'User-Agent': USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            tile_data = response.read()
            with open(tile_file, 'wb') as f:
                f.write(tile_data)
        return True
    except urllib.error.URLError as e:
        print(f'  Error downloading {url}: {e}')
        return False
    except Exception as e:
        print(f'  Unexpected error for {url}: {e}')
        return False


def main():
    """Download and cache OSM tiles for the demo area."""
    print(f'Caching OSM tiles for coordinates: {CENTER_LAT}, {CENTER_LON}')
    print(f'Zoom levels: {ZOOM_LEVELS}')
    print(f'Grid size: {GRID_SIZE}x{GRID_SIZE} tiles per zoom level')
    print()
    
    total_cached = 0
    
    for zoom in ZOOM_LEVELS:
        print(f'Zoom level {zoom}:')
        
        # Get center tile coordinates for this zoom level
        center_x, center_y = lat_lon_to_tile(CENTER_LAT, CENTER_LON, zoom)
        
        # Define grid bounds (5x5 around center)
        half_grid = GRID_SIZE // 2
        x_start = center_x - half_grid
        x_end = center_x + half_grid
        y_start = center_y - half_grid
        y_end = center_y + half_grid
        
        print(f'  Center tile: ({center_x}, {center_y})')
        print(f'  X range: {x_start} to {x_end}')
        print(f'  Y range: {y_start} to {y_end}')
        
        zoom_cached = 0
        
        for x in range(x_start, x_end + 1):
            for y in range(y_start, y_end + 1):
                if download_tile(x, y, zoom):
                    zoom_cached += 1
                    total_cached += 1
                
                # Sleep between requests to respect tile server
                time.sleep(REQUEST_DELAY_MS / 1000)
        
        print(f'  Cached {zoom_cached} new tiles for zoom {zoom}')
        print()
    
    print(f'Total tiles cached: {total_cached}')
    print(f'Tiles stored in: {os.path.abspath(TILE_CACHE_DIR)}')


if __name__ == '__main__':
    main()
