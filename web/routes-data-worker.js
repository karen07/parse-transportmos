function unpackCompactPayload(data) {
    function base64Bytes(value) {
        const raw = atob(value);
        const bytes = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) {
            bytes[i] = raw.charCodeAt(i);
        }
        return bytes;
    }

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
    if (transfers && transfers.format === 'csr-delta-varint-cm-v1') {
        const input = reader(base64Bytes(transfers.arcs));
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
    if (packed && packed.format === 'route-delta-varint-e7-v1') {
        const input = reader(base64Bytes(packed.data));
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
        for (const route of Object.values(data.routes)) {
            for (const direction of route.directions) {
                if (typeof direction.geometry === 'number') {
                    const geometry = geometries[direction.geometry];
                    if (!geometry) {
                        throw new Error('Unknown transport geometry index');
                    }
                    direction.geometry = geometry;
                }
            }
        }
        delete data.route_geometries;
    }
}

async function decodeTransportBytes(bytes) {
    if (!bytes.length) throw new Error('Downloaded data file is empty');
    let text;
    if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
        if (typeof DecompressionStream !== 'function') {
            throw new Error('This browser requires DecompressionStream(gzip)');
        }
        const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
        text = await new Response(stream).text();
    } else {
        // Accept gzip already decompressed by an HTTP Content-Encoding header.
        text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    }
    self.postMessage({
        type: 'phase',
        stage: 'PARSING JSON',
        detail: 'Decoding the transport network...',
    });
    const data = JSON.parse(text);
    unpackCompactPayload(data);
    if (data.walking && data.walking.format === 'delta-varint-dm-v1') {
        const graph = data.walking;
        const decode = (value) => {
            const raw = atob(value);
            const bytes = new Uint8Array(raw.length);
            for (let i = 0; i < raw.length; i++) {
                bytes[i] = raw.charCodeAt(i);
            }
            return bytes;
        };
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
        const xy = reader(decode(graph.nodes));
        const arcs = reader(decode(graph.edges));
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

// Graph decode uses transferable typed arrays, not deep structured cloning.
self.onmessage = async (event) => {
    try {
        const { url } = event.data;
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
        self.postMessage({ type: 'ready', data, decodeMs }, transfer);
    } catch (error) {
        self.postMessage({ type: 'error', message: String(error.message || error) });
    }
};
