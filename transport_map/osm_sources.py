"""Osm sources for the Moscow transport map."""

import json
import time
import urllib.error
import urllib.request

from .common import (
    MERGED_MANIFEST_NAME,
    MERGED_SOURCE_NAME,
    PROJECT_VERSION,
    SOURCE_REGIONS,
    TRANSPORT_CACHE_NAME,
    osmium,
)
from .transport import TransportMasterDiscovery, TransportSelectionHandler


def download_source(url, source_path):
    """Download one regional PBF with retries, HTTP Range resume and atomic replace."""
    source_path.parent.mkdir(parents=True, exist_ok=True)
    part = source_path.with_name(source_path.name + ".part")
    metadata_path = part.with_name(part.name + ".json")
    for attempt in range(1, 6):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            metadata = {}
        # Avoid appending a newer region snapshot to an obsolete partial file.
        validator = metadata.get("validator") if metadata.get("url") == url else None
        if not validator:
            part.unlink(missing_ok=True)
        offset = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": f"moscow-transport-reachability/{PROJECT_VERSION}"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            headers["If-Range"] = validator
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                status = response.status
                content_range = response.headers.get("Content-Range", "")
                if status == 206:
                    if not content_range.startswith(f"bytes {offset}-"):
                        raise RuntimeError("Unexpected Content-Range from " + url)
                    returned_validator = response.headers.get(
                        "ETag"
                    ) or response.headers.get("Last-Modified")
                    if (
                        validator
                        and returned_validator
                        and returned_validator != validator
                    ):
                        part.unlink(missing_ok=True)
                        metadata_path.unlink(missing_ok=True)
                        raise RuntimeError("Regional PBF changed during HTTP resume")
                    mode = "ab"
                elif status == 200:
                    # A server may ignore Range: restart instead of appending.
                    offset = 0
                    mode = "wb"
                else:
                    raise RuntimeError(f"Unexpected HTTP status {status}: {url}")
                validator = response.headers.get("ETag") or response.headers.get(
                    "Last-Modified"
                )
                if validator:
                    metadata_path.write_text(
                        json.dumps({"url": url, "validator": validator}),
                        encoding="utf-8",
                    )
                else:
                    metadata_path.unlink(missing_ok=True)
                content_length = response.headers.get("Content-Length")
                total = offset + int(content_length) if content_length else None
                downloaded = offset
                next_report = ((downloaded // (64 * 1048576)) + 1) * 64 * 1048576
                with part.open(mode) as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        if downloaded >= next_report:
                            suffix = f" / {total // 1048576} MiB" if total else ""
                            print(
                                f"[{source_path.name}] {downloaded // 1048576}"
                                f" MiB{suffix}",
                                flush=True,
                            )
                            next_report += 64 * 1048576
                if total is not None and downloaded != total:
                    raise OSError(f"Truncated download: {downloaded}/{total} bytes")
            if downloaded == 0:
                raise OSError("Received an empty PBF")
            part.replace(source_path)
            metadata_path.unlink(missing_ok=True)
            print(f"Downloaded: {source_path} ({downloaded // 1048576} MiB)")
            return
        except (OSError, urllib.error.URLError, RuntimeError) as error:
            if isinstance(error, urllib.error.HTTPError):
                if error.code == 416:
                    part.unlink(missing_ok=True)
                    metadata_path.unlink(missing_ok=True)
                # A permanent HTTP error will not be fixed by five retries.
                if error.code in (400, 401, 403, 404, 410):
                    raise RuntimeError(
                        f"OSM source rejected the request (HTTP {error.code}): {url}. "
                        "Check the URL or use a different OSM mirror."
                    ) from error
            if attempt == 5:
                raise RuntimeError(f"Failed to download {url}: {error}") from error
            print(f"Download retry {attempt}/5 for {url}: {error}", flush=True)
            time.sleep(3)


def source_signature(paths):
    """Manifest key invalidating the merged PBF after a region changes."""
    return [
        {
            "name": path.name,
            "bytes": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
        }
        for path in paths
    ]


def osm_object_key(obj):
    """Sort nodes, ways and relations by type and then original OSM ID."""
    ranks = {"n": 0, "w": 1, "r": 2}
    return ranks[obj.type_str()], obj.id


def merge_osm_pbf_files(sources, destination):
    """Merge sorted regional extracts with bounded memory using pyosmium.

    Keep one current OSM object per source, writing it before advancing that
    source's iterator. This matters because pyosmium returns temporary views.
    If extracts overlap, prefer the higher version of the same OSM object.
    """
    if not hasattr(osmium, "FileProcessor"):
        raise RuntimeError(
            "The OSMlab merge needs pyosmium 4.x (python -m pip install -U osmium)"
        )
    if len(sources) != 2:
        raise ValueError("Expected the two Moscow regional PBF files")

    city = iter(osmium.FileProcessor(str(sources[0])))
    oblast = iter(osmium.FileProcessor(str(sources[1])))
    city_obj = next(city, None)
    oblast_obj = next(oblast, None)
    written = 0
    duplicates = 0
    differing_versions = 0
    previous = None

    with osmium.SimpleWriter(str(destination)) as writer:
        while city_obj is not None or oblast_obj is not None:
            city_key = osm_object_key(city_obj) if city_obj is not None else None
            oblast_key = osm_object_key(oblast_obj) if oblast_obj is not None else None
            if oblast_key is None or (city_key is not None and city_key < oblast_key):
                key = city_key
                chosen = city_obj
                take_city, take_oblast = True, False
            elif city_key is None or oblast_key < city_key:
                key = oblast_key
                chosen = oblast_obj
                take_city, take_oblast = False, True
            else:
                key = city_key
                duplicates += 1
                if city_obj.version != oblast_obj.version:
                    differing_versions += 1
                chosen = (
                    city_obj if city_obj.version >= oblast_obj.version else oblast_obj
                )
                take_city, take_oblast = True, True

            if previous is not None and key <= previous:
                raise RuntimeError(
                    "Unsorted or duplicated OSM objects in source PBF: "
                    f"{key}. Expected strictly increasing type/ID order."
                )
            # The writer must copy the chosen view before either iterator moves.
            writer.add(chosen)
            written += 1
            previous = key
            if take_city:
                city_obj = next(city, None)
            if take_oblast:
                oblast_obj = next(oblast, None)
            if written % 2000000 == 0:
                print(f"[merge] {written:,} OSM objects written", flush=True)

    if differing_versions:
        print(
            f"[merge] WARNING: {differing_versions} overlapping objects "
            "have different versions; confirm the regional snapshot dates",
            flush=True,
        )
    print(
        f"[merge] {written:,} objects, {duplicates:,} shared IDs removed",
        flush=True,
    )
    return written


def merge_regional_sources(cache_dir, sources, rebuild=False):
    """Cache a sorted, ID-deduplicated union of the two OSM regional extracts."""
    target = cache_dir / MERGED_SOURCE_NAME
    manifest = cache_dir / MERGED_MANIFEST_NAME
    signature = source_signature(sources)
    if not rebuild and target.is_file() and manifest.is_file():
        try:
            if json.loads(manifest.read_text(encoding="utf-8")) == signature:
                print(f"Using cached regional union: {target}")
                return target, False
        except (OSError, ValueError):
            pass

    tmp = cache_dir / "moscow-regions-osmlab.tmp.osm.pbf"
    manifest_tmp = manifest.with_name(manifest.name + ".tmp")
    tmp.unlink(missing_ok=True)
    manifest_tmp.unlink(missing_ok=True)
    print("Merging Moscow city + Moscow Oblast using pyosmium...", flush=True)
    try:
        count = merge_osm_pbf_files(sources, tmp)
        if not count or not tmp.is_file() or not tmp.stat().st_size:
            raise RuntimeError("Merged OSM PBF is empty")
        manifest_tmp.write_text(json.dumps(signature, indent=2) + "\n", "utf-8")
        tmp.replace(target)
        manifest_tmp.replace(manifest)
    finally:
        tmp.unlink(missing_ok=True)
        manifest_tmp.unlink(missing_ok=True)
    print(f"Merged regional OSM: {target}")
    return target, True


def build_transport_extract(source_path, transport_path):
    tmp_path = transport_path.with_name(
        transport_path.stem + ".tmp" + transport_path.suffix
    )
    tmp_path.unlink(missing_ok=True)

    try:
        with osmium.BackReferenceWriter(
            str(tmp_path),
            ref_src=str(source_path),
            overwrite=True,
            remove_tags=False,
            relation_depth=1,
        ) as writer:
            discovery = TransportMasterDiscovery()
            discovery.apply_file(str(source_path))
            selector = TransportSelectionHandler(writer, discovery.child_modes)
            selector.apply_file(str(source_path))

        if selector.selected_masters == 0 and selector.selected_routes == 0:
            raise RuntimeError("No supported transport relations found in source PBF")

        tmp_path.replace(transport_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    print(f"selected route masters: {selector.selected_masters}")
    print(f"selected direct routes: {selector.selected_routes}")


def prepare_raw_source(cache_dir, update=False):
    cache_dir.mkdir(parents=True, exist_ok=True)
    sources = []
    for region, url in SOURCE_REGIONS:
        source = cache_dir / f"{region}.osm.pbf"
        if update or not source.is_file():
            print(f"Downloading OSM region {region}: {url}", flush=True)
            download_source(url, source)
        else:
            print(f"Using cached: {source}")
        sources.append(source)

    return merge_regional_sources(cache_dir, sources)


def build_cache(cache_dir, update=False, rebuild=False):
    source, source_changed = prepare_raw_source(cache_dir, update)
    transport_pbf = cache_dir / TRANSPORT_CACHE_NAME
    if update or rebuild or source_changed or not transport_pbf.is_file():
        print("Selecting all bus/tram/trolleybus networks and urban rail routes...")
        build_transport_extract(source, transport_pbf)
    else:
        print(f"Using cached: {transport_pbf}")
    return transport_pbf, source
