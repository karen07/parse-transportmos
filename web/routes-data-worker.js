function unpackCompactPayload(data) {
    function reader(bytes) {
        let position = 0;
        return {
            unsigned() {
                let result = 0;
                let factor = 1;
                for (let part = 0; part < 5; part++) {
                    if (position >= bytes.length) {
                        throw new Error('Truncated compact transport data');
                    }
                    const byte = bytes[position++];
                    result += (byte & 127) * factor;
                    if (byte < 128) return result;
                    factor *= 128;
                }
                throw new Error('Invalid transport varint');
            },
            signed() {
                const value = this.unsigned();
                return value % 2 === 0 ? value / 2 : -(value + 1) / 2;
            },
            byte() {
                if (position >= bytes.length) {
                    throw new Error('Truncated transport geometry');
                }
                return bytes[position++];
            },
            complete() {
                return position === bytes.length;
            },
        };
    }

    const transfers = data.walk_transfers;
    if (transfers) {
        if (transfers.format !== 'csr-delta-varint-cm' || !(transfers.arcs instanceof Uint8Array)) {
            throw new Error('Invalid transfer format');
        }
        const input = reader(transfers.arcs);
        const offsets = new Uint32Array(transfers.stop_count + 1);
        const to = new Uint32Array(transfers.arc_count);
        const meters = new Float64Array(transfers.arc_count);
        let cursor = 0;
        for (let stop = 0; stop < transfers.stop_count; stop++) {
            const degree = input.unsigned();
            if (degree > transfers.arc_count - cursor) {
                throw new Error('Invalid walking transfer degree');
            }
            let previous = 0;
            for (let j = 0; j < degree; j++) {
                const target = previous + input.unsigned();
                if (target >= transfers.stop_count) {
                    throw new Error('Invalid walking transfer target');
                }
                to[cursor] = target;
                meters[cursor] = input.unsigned() / 100;
                previous = target;
                cursor++;
            }
            offsets[stop + 1] = cursor;
        }
        if (cursor !== transfers.arc_count || !input.complete()) {
            throw new Error('Invalid walking transfer count');
        }
        data.walk_transfers = { offsets, to, meters };
    }

    const packed = data.route_geometries;
    if (packed) {
        if (packed.format !== 'route-delta-varint-e7' || !(packed.data instanceof Uint8Array)) {
            throw new Error('Invalid geometry format');
        }
        const input = reader(packed.data);
        const geometries = [];
        for (let i = 0; i < packed.count; i++) {
            const type = input.byte();
            if (type !== 1 && type !== 2) {
                throw new Error('Invalid transport geometry type');
            }
            const count = input.unsigned();
            const lines = [];
            for (let j = 0; j < count; j++) {
                const pointCount = input.unsigned();
                const line = [];
                let lon = 0;
                let lat = 0;
                for (let k = 0; k < pointCount; k++) {
                    lon += input.signed();
                    lat += input.signed();
                    line.push([lon / 10000000, lat / 10000000]);
                }
                lines.push(line);
            }
            geometries.push(
                type === 1
                    ? { type: 'LineString', coordinates: lines[0] }
                    : { type: 'MultiLineString', coordinates: lines },
            );
        }
        if (!input.complete()) {
            throw new Error('Trailing transport geometry bytes');
        }
        if (data.routes) throw new Error('Geometry must be a separate resource');
        data.geometries = geometries;
        delete data.route_geometries;
    }
}

async function decodeTransportBytes(bytes) {
    if (!bytes.length) throw new Error('Downloaded data file is empty');
    if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
        if (typeof DecompressionStream !== 'function') {
            throw new Error('This browser requires DecompressionStream(gzip)');
        }
        const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
        bytes = new Uint8Array(await new Response(stream).arrayBuffer());
    }
    const decoder = new TextDecoder('utf-8', { fatal: true });
    const binary =
        bytes.length >= 12 &&
        bytes[0] === 84 &&
        bytes[1] === 77 &&
        decoder.decode(bytes.subarray(0, 8)) === 'TMAPDATA';
    self.postMessage({
        type: 'phase',
        stage: 'PARSING JSON',
        detail: 'Decoding the transport network...',
    });
    if (!binary) throw new Error('Invalid binary network magic');
    let data;
    {
        const length = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(
            8,
            true,
        );
        let cursor = 12 + length;
        if (cursor > bytes.length) throw new Error('Truncated binary network header');
        data = JSON.parse(decoder.decode(bytes.subarray(12, cursor)));
        const allowed = new Set([
            'walking.nodes',
            'walking.edges',
            'walk_transfers.arcs',
            'route_geometries.data',
        ]);
        if (!Array.isArray(data.binary_sections)) throw new Error('Missing binary sections');
        for (const [group, key, size] of data.binary_sections) {
            if (
                !allowed.delete(group + '.' + key) ||
                !data[group] ||
                !Number.isSafeInteger(size) ||
                size < 0 ||
                size > bytes.length - cursor
            ) {
                throw new Error('Invalid binary network section');
            }
            data[group][key] = bytes.subarray(cursor, cursor + size);
            cursor += size;
        }
        if (cursor !== bytes.length) throw new Error('Trailing binary network bytes');
        delete data.binary_sections;
    }
    unpackCompactPayload(data);
    if (data.walking) {
        const graph = data.walking;
        if (
            graph.format !== 'delta-varint-dm' ||
            !(graph.nodes instanceof Uint8Array) ||
            !(graph.edges instanceof Uint8Array)
        ) {
            throw new Error('Invalid walking graph format');
        }
        const reader = (bytes) => {
            let position = 0;
            return {
                unsigned() {
                    let result = 0;
                    let factor = 1;
                    for (let part = 0; part < 5; part++) {
                        if (position >= bytes.length) {
                            throw new Error('Truncated walking graph varint');
                        }
                        const byte = bytes[position++];
                        result += (byte & 127) * factor;
                        if (byte < 128) return result;
                        factor *= 128;
                    }
                    throw new Error('Invalid walking graph varint');
                },
                signed() {
                    const value = this.unsigned();
                    return value % 2 === 0 ? value / 2 : -(value + 1) / 2;
                },
                byte() {
                    if (position >= bytes.length) throw new Error('Missing graph direction');
                    return bytes[position++];
                },
                complete() {
                    return position === bytes.length;
                },
            };
        };
        const xy = reader(graph.nodes);
        const arcs = reader(graph.edges);
        // Packed, transferable arrays avoid cloning millions of small JS arrays.
        const nodeXY = new Int32Array(graph.node_count * 2);
        const edgeAB = new Uint32Array(graph.edge_count * 2);
        const edgeLengths = new Float64Array(graph.edge_count);
        const edgeDirs = new Uint8Array(graph.edge_count);
        let x = 0,
            y = 0,
            previousA = 0;
        for (let i = 0; i < graph.node_count; i++) {
            x += xy.signed();
            y += xy.signed();
            nodeXY[2 * i] = x;
            nodeXY[2 * i + 1] = y;
        }
        for (let i = 0; i < graph.edge_count; i++) {
            const a = previousA + arcs.signed();
            const b = a + arcs.signed();
            const length = arcs.unsigned() / 10;
            const direction = arcs.byte();
            if (a < 0 || a >= graph.node_count || b < 0 || b >= graph.node_count) {
                throw new Error('Walking graph edge references invalid node');
            }
            edgeAB[2 * i] = a;
            edgeAB[2 * i + 1] = b;
            edgeLengths[i] = length;
            edgeDirs[i] = direction;
            previousA = a;
        }
        if (!xy.complete() || !arcs.complete()) {
            throw new Error('Trailing walking graph data');
        }
        data.walking = {
            nodeXY,
            edgeAB,
            edgeLengths,
            edgeDirs,
            nodeCount: graph.node_count,
            edgeCount: graph.edge_count,
        };
    }
    return data;
}

// Build adjacency off the UI thread; all three buffers are transferred.
function buildWalkingCSR(walking) {
    const walkNodes = { length: walking.nodeCount };
    const walkEdges = { length: walking.edgeCount };
    const walkEdgeAB = walking.edgeAB;
    const walkEdgeDirs = walking.edgeDirs;
    const walkEdgeLengths = walking.edgeLengths;
    const walkDegree = new Uint32Array(walkNodes.length);
    for (let i = 0; i < walkEdges.length; ++i) {
        const direction = walkEdgeDirs[i];
        if (direction !== 3 && direction !== 13) walkDegree[walkEdgeAB[2 * i]]++;
        if (direction !== 2 && direction !== 12) walkDegree[walkEdgeAB[2 * i + 1]]++;
    }
    const walkOffsets = new Uint32Array(walkNodes.length + 1);
    for (let i = 0; i < walkNodes.length; ++i) {
        walkOffsets[i + 1] = walkOffsets[i] + walkDegree[i];
    }
    const walkTo = new Uint32Array(walkOffsets[walkNodes.length]);
    const walkWeight = new Float32Array(walkTo.length);
    const walkNext = walkOffsets.slice(0, walkNodes.length);
    for (let i = 0; i < walkEdges.length; ++i) {
        const a = walkEdgeAB[2 * i],
            b = walkEdgeAB[2 * i + 1];
        const length = walkEdgeLengths[i],
            direction = walkEdgeDirs[i];
        if (direction !== 3 && direction !== 13) {
            const slot = walkNext[a]++;
            walkTo[slot] = b;
            walkWeight[slot] = length;
        }
        if (direction !== 2 && direction !== 12) {
            const slot = walkNext[b]++;
            walkTo[slot] = a;
            walkWeight[slot] = length;
        }
    }

    walking.offsets = walkOffsets;
    walking.to = walkTo;
    walking.weight = walkWeight;
}

// Graph decode uses transferable typed arrays, not deep structured cloning.
self.onmessage = async (event) => {
    try {
        const { url, geometryOnly } = event.data;
        self.postMessage({
            type: 'phase',
            stage: 'FETCHING DATA',
            detail: 'Downloading the compressed transport graph...',
        });
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error('HTTP ' + response.status + ' for ' + url);
        }
        const header = Number(response.headers.get('Content-Length'));
        // fetch() may transparently decode Content-Encoding. Then the
        // encoded Content-Length is not a valid progress denominator.
        const encoded = response.headers.has('Content-Encoding');
        const total = !encoded && Number.isFinite(header) && header > 0 ? header : 0;
        const parts = [];
        let received = 0,
            lastReport = 0;
        if (response.body) {
            const reader = response.body.getReader();
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                if (!value || !value.byteLength) continue;
                parts.push(value);
                received += value.byteLength;
                if (Date.now() - lastReport >= 75) {
                    self.postMessage({ type: 'progress', received, total });
                    lastReport = Date.now();
                }
            }
        } else {
            const arrayBuffer = await response.arrayBuffer();
            parts.push(new Uint8Array(arrayBuffer));
            received = arrayBuffer.byteLength;
        }
        self.postMessage({ type: 'progress', received, total });
        if (total && received !== total) {
            throw new Error('Incomplete data: ' + received + '/' + total);
        }
        const bytes = new Uint8Array(received);
        let cursor = 0;
        for (const part of parts) {
            bytes.set(part, cursor);
            cursor += part.byteLength;
        }
        self.postMessage({
            type: 'phase',
            stage: 'DECOMPRESSING',
            detail: 'Unpacking gzip (running in a Web Worker)...',
        });
        const decodedStarted = performance.now();
        const data = await decodeTransportBytes(bytes);
        const decodeMs = Math.round(performance.now() - decodedStarted);
        if (geometryOnly) {
            if (!Array.isArray(data.geometries)) throw new Error('Missing route geometry');
            self.postMessage({ type: 'ready', data, decodeMs });
            return;
        }
        const csrStarted = performance.now();
        if (!data.walking || !data.walk_transfers || !data.routes || !Array.isArray(data.stops)) {
            throw new Error('Missing required network data');
        }
        buildWalkingCSR(data.walking);
        const csrMs = Math.round(performance.now() - csrStarted);
        const transfer = [];
        for (const value of Object.values(data.walking)) {
            if (ArrayBuffer.isView(value)) transfer.push(value.buffer);
        }
        for (const value of Object.values(data.walk_transfers || {})) {
            if (ArrayBuffer.isView(value)) transfer.push(value.buffer);
        }
        self.postMessage({
            type: 'phase',
            stage: 'BUILDING GRAPH',
            detail: 'Transferring binary graph buffers to the map...',
        });
        self.postMessage({ type: 'ready', data, decodeMs, csrMs }, transfer);
    } catch (error) {
        self.postMessage({ type: 'error', message: String(error.message || error) });
    }
};
