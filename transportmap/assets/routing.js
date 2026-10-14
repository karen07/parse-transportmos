/*
 * RAPTOR-style reachability engine.
 * Round 0 = first ride, round 1 = one transfer, round 2 = two transfers.
 * Travel-time changes only filter cached labels; increasing the transfer
 * limit computes only the missing round.
 */
function reachabilitySearchKey() {
    if (!selectedPoint) {
        return "";
    }

    return [
        selectedPoint.lat.toFixed(6),
        selectedPoint.lon.toFixed(6),
        walkRadius.toFixed(1),
        Array.from(enabledModes).sort().join(",")
    ].join(":");
}

function cloneLegParts(parts) {
    return parts.map(part => ({ ...part }));
}

function initialRideLeg(routeKey, direction, boardPos) {
    const stopIndex = direction.stops[boardPos];

    return {
        routeKey: routeKey,
        direction: direction,
        startStopIndex: stopIndex,
        endStopIndex: stopIndex,
        startPos: boardPos,
        endPos: boardPos,
        rideStops: 0,
        parts: [{
            routeKey: routeKey,
            direction: direction,
            startPos: boardPos,
            endPos: boardPos
        }]
    };
}

function extendRideLeg(leg, direction, nextPos) {
    const parts = cloneLegParts(leg.parts);
    const last = parts[parts.length - 1];
    last.endPos = nextPos;

    return {
        ...leg,
        endStopIndex: direction.stops[nextPos],
        endPos: nextPos,
        rideStops: leg.rideStops + 1,
        parts: parts
    };
}

function continueRideLeg(leg, nextDirection) {
    const parts = cloneLegParts(leg.parts);
    parts.push({
        routeKey: leg.routeKey,
        direction: nextDirection,
        startPos: 0,
        endPos: 0
    });

    return {
        ...leg,
        endStopIndex: nextDirection.stops[0],
        endPos: 0,
        parts: parts
    };
}

function itineraryFromRaptorState(state) {
    const legs = state.legs.concat([state.currentLeg]);
    const transferCount = state.transfersUsed;
    const walkMinutes = state.walkMeters / WALK_SPEED_M_PER_MIN;
    const candidate = {
        type:
            transferCount === 0
                ? "direct"
                : transferCount === 1
                    ? "transfer"
                    : "two-transfer",
        walkMeters: state.walkMeters,
        walkMinutes: walkMinutes,
        rideStops: state.rideStops,
        initialWalk: state.initialWalk,
        transitMeters: state.transitMeters,
        boardings: transferCount + 1,
        totalMinutes: state.elapsedMinutes,
        generalizedMinutes:
            state.elapsedMinutes + walkMinutes * WALK_PREFERENCE_EXTRA,
        leg1: legs[0]
    };

    if (transferCount >= 1) {
        const transfer = state.transfers[0];
        candidate.leg2 = legs[1];
        candidate.transferWalk = transfer.walkMeters;
        candidate.transferAlightStopIndex = transfer.alightStopIndex;
        candidate.transferBoardStopIndex = transfer.boardStopIndex;
    }

    if (transferCount >= 2) {
        const transfer = state.transfers[1];
        candidate.leg3 = legs[2];
        candidate.secondTransferWalk = transfer.walkMeters;
        candidate.secondTransferAlightStopIndex =
            transfer.alightStopIndex;
        candidate.secondTransferBoardStopIndex =
            transfer.boardStopIndex;
    }

    return candidate;
}

function candidateLegs(candidate) {
    const result = [];

    if (candidate.leg1) {
        result.push(candidate.leg1);
    }

    if (candidate.leg2) {
        result.push(candidate.leg2);
    }

    if (candidate.leg3) {
        result.push(candidate.leg3);
    }

    return result;
}

function candidateTransfers(candidate) {
    const result = [];

    if (candidate.leg2) {
        result.push({
            alightStopIndex: candidate.transferAlightStopIndex,
            boardStopIndex: candidate.transferBoardStopIndex,
            walkMeters: candidate.transferWalk || 0
        });
    }

    if (candidate.leg3) {
        result.push({
            alightStopIndex:
                candidate.secondTransferAlightStopIndex,
            boardStopIndex:
                candidate.secondTransferBoardStopIndex,
            walkMeters: candidate.secondTransferWalk || 0
        });
    }

    return result;
}

function searchEvidencePathKey(candidate) {
    return candidateLegs(candidate)
        .map(
            leg =>
                leg.routeKey +
                ":" +
                String(legStartStopIndex(leg)) +
                "-" +
                String(legEndStopIndex(leg)) +
                ":" +
                routeLegPathKey(leg)
        )
        .join(">");
}

function updateSearchEvidence(evidence, stopIndex, candidate) {
    const pathKey = searchEvidencePathKey(candidate);
    let byPath = evidence.get(stopIndex);

    if (!byPath) {
        byPath = new Map();
        evidence.set(stopIndex, byPath);
    }

    let bucket = byPath.get(pathKey) || [];

    if (bucket.some(item => itineraryDominates(item, candidate))) {
        return;
    }

    bucket = bucket.filter(
        item => !itineraryDominates(candidate, item)
    );
    bucket.push(candidate);
    bucket.sort(compareItineraries);

    if (bucket.length > MAX_EVIDENCE_PER_PATH) {
        bucket = bucket.slice(0, MAX_EVIDENCE_PER_PATH);
    }

    byPath.set(pathKey, bucket);
}

function raptorSourcePathKey(source) {
    if (!source.legs || source.legs.length === 0) {
        return "walk";
    }

    return source.legs
        .map(
            leg =>
                leg.routeKey +
                ":" +
                String(legStartStopIndex(leg)) +
                "-" +
                String(legEndStopIndex(leg))
        )
        .join(">");
}

function raptorSourceDominates(a, b) {
    const noSlower = a.elapsedMinutes <= b.elapsedMinutes + 1e-7;
    const noMoreWalk = a.walkMeters <= b.walkMeters + 1.0;
    const strictlyBetter =
        a.elapsedMinutes < b.elapsedMinutes - 1e-7 ||
        a.walkMeters < b.walkMeters - 1.0;

    return noSlower && noMoreWalk && strictlyBetter;
}

function raptorSourceScore(source) {
    return (
        source.elapsedMinutes +
        source.walkMeters /
            WALK_SPEED_M_PER_MIN * WALK_PREFERENCE_EXTRA
    );
}

function addRaptorTransferSource(
    sourcesByStop,
    stopIndex,
    source
) {
    let byLastRoute = sourcesByStop.get(stopIndex);

    if (!byLastRoute) {
        byLastRoute = new Map();
        sourcesByStop.set(stopIndex, byLastRoute);
    }

    const lastRouteKey = source.lastRouteKey || "";
    let bucket = byLastRoute.get(lastRouteKey) || [];

    if (bucket.some(item => raptorSourceDominates(item, source))) {
        return false;
    }

    bucket = bucket.filter(
        item => !raptorSourceDominates(source, item)
    );

    const pathKey = raptorSourcePathKey(source);
    const samePathIndex = bucket.findIndex(
        item => raptorSourcePathKey(item) === pathKey
    );

    if (samePathIndex >= 0) {
        const current = bucket[samePathIndex];

        if (raptorSourceScore(current) <= raptorSourceScore(source)) {
            return false;
        }

        bucket.splice(samePathIndex, 1);
    }

    bucket.push(source);
    bucket.sort(
        (a, b) => raptorSourceScore(a) - raptorSourceScore(b)
    );

    if (bucket.length > MAX_RAPTOR_TRANSFER_LABELS_PER_ROUTE) {
        bucket = bucket.slice(0, MAX_RAPTOR_TRANSFER_LABELS_PER_ROUTE);
    }

    byLastRoute.set(lastRouteKey, bucket);

    const all = [];

    for (const [routeKey, routeBucket] of byLastRoute.entries()) {
        for (const item of routeBucket) {
            all.push({ routeKey: routeKey, source: item });
        }
    }

    if (all.length > MAX_RAPTOR_TRANSFER_LABELS_PER_STOP) {
        all.sort(
            (a, b) =>
                raptorSourceScore(a.source) -
                raptorSourceScore(b.source)
        );
        const keep = new Set(
            all
                .slice(0, MAX_RAPTOR_TRANSFER_LABELS_PER_STOP)
                .map(item => item.source)
        );

        for (const [routeKey, routeBucket] of byLastRoute.entries()) {
            const filtered = routeBucket.filter(item => keep.has(item));

            if (filtered.length) {
                byLastRoute.set(routeKey, filtered);
            } else {
                byLastRoute.delete(routeKey);
            }
        }
    }

    return true;
}

function flattenRaptorSources(byLastRoute) {
    const result = [];

    if (!byLastRoute) {
        return result;
    }

    for (const bucket of byLastRoute.values()) {
        result.push(...bucket);
    }

    return result;
}

function raptorOnboardNodeKey(state) {
    return [
        state.routeKey,
        relationKey(state.direction),
        String(state.pos)
    ].join("|");
}

function raptorOnboardPathKey(state) {
    return [
        ...state.legs.map(leg => leg.routeKey),
        state.routeKey,
        String(legStartStopIndex(state.currentLeg))
    ].join(">");
}

function raptorOnboardDominates(a, b) {
    const noSlower = a.elapsedMinutes <= b.elapsedMinutes + 1e-7;
    const noMoreWalk = a.walkMeters <= b.walkMeters + 1.0;
    const strictlyBetter =
        a.elapsedMinutes < b.elapsedMinutes - 1e-7 ||
        a.walkMeters < b.walkMeters - 1.0;

    return noSlower && noMoreWalk && strictlyBetter;
}

function raptorOnboardScore(state) {
    return (
        state.elapsedMinutes +
        state.walkMeters /
            WALK_SPEED_M_PER_MIN * WALK_PREFERENCE_EXTRA
    );
}

function addRaptorOnboardState(nodeLabels, queue, state) {
    const nodeKey = raptorOnboardNodeKey(state);
    let bucket = nodeLabels.get(nodeKey) || [];

    if (bucket.some(item => raptorOnboardDominates(item, state))) {
        return false;
    }

    bucket = bucket.filter(
        item => !raptorOnboardDominates(state, item)
    );

    const pathKey = raptorOnboardPathKey(state);
    const samePathIndex = bucket.findIndex(
        item => raptorOnboardPathKey(item) === pathKey
    );

    if (samePathIndex >= 0) {
        const current = bucket[samePathIndex];

        if (raptorOnboardScore(current) <= raptorOnboardScore(state)) {
            return false;
        }

        bucket.splice(samePathIndex, 1);
    }

    bucket.push(state);
    bucket.sort(
        (a, b) => raptorOnboardScore(a) - raptorOnboardScore(b)
    );

    if (bucket.length > MAX_RAPTOR_ONBOARD_LABELS) {
        bucket = bucket.slice(0, MAX_RAPTOR_ONBOARD_LABELS);
    }

    if (!bucket.includes(state)) {
        nodeLabels.set(nodeKey, bucket);
        return false;
    }

    nodeLabels.set(nodeKey, bucket);
    queue.push(state);
    return true;
}

function seedRaptorRound(boardSources, roundIndex) {
    const nodeLabels = new Map();
    const queue = [];

    for (const [boardStopIndex, byLastRoute] of boardSources.entries()) {
        const sources = flattenRaptorSources(byLastRoute);

        for (const source of sources) {
            for (const option of boardingOptionsAtStop(boardStopIndex)) {
                if (
                    source.lastRouteKey &&
                    source.lastRouteKey === option.routeKey
                ) {
                    continue;
                }

                addRaptorOnboardState(nodeLabels, queue, {
                    routeKey: option.routeKey,
                    direction: option.direction,
                    pos: option.boardPos,
                    transfersUsed: roundIndex,
                    elapsedMinutes:
                        source.elapsedMinutes + boardingWaitForRoute(option.routeKey),
                    walkMeters: source.walkMeters,
                    initialWalk: source.initialWalk,
                    rideStops: source.rideStops,
                    transitMeters: source.transitMeters,
                    legs: source.legs,
                    currentLeg: initialRideLeg(
                        option.routeKey,
                        option.direction,
                        option.boardPos
                    ),
                    transfers: source.transfers
                });
            }
        }
    }

    return { nodeLabels: nodeLabels, queue: queue };
}

function scanRaptorRound(boardSources, roundIndex) {
    const seeded = seedRaptorRound(boardSources, roundIndex);
    const nodeLabels = seeded.nodeLabels;
    const queue = seeded.queue;
    const evidence = new Map();
    let head = 0;

    while (head < queue.length) {
        const state = queue[head++];
        const nodeKey = raptorOnboardNodeKey(state);
        const currentBucket = nodeLabels.get(nodeKey);

        if (!currentBucket || !currentBucket.includes(state)) {
            continue;
        }

        const currentStopIndex = state.direction.stops[state.pos];

        if (
            state.currentLeg.rideStops > 0 &&
            stopAllowsAlighting(state.direction, state.pos)
        ) {
            updateSearchEvidence(
                evidence,
                currentStopIndex,
                itineraryFromRaptorState(state)
            );
        }

        if (state.pos + 1 < state.direction.stops.length) {
            const metrics = routeEdgeMetrics(
                state.routeKey,
                state.direction,
                state.pos
            );

            if (!metrics) {
                continue;
            }

            const nextPos = state.pos + 1;
            addRaptorOnboardState(nodeLabels, queue, {
                ...state,
                pos: nextPos,
                elapsedMinutes:
                    state.elapsedMinutes + metrics.minutes,
                rideStops: state.rideStops + 1,
                transitMeters:
                    state.transitMeters + metrics.distance,
                currentLeg: extendRideLeg(
                    state.currentLeg,
                    state.direction,
                    nextPos
                )
            });
            continue;
        }

        const route = routes[state.routeKey];
        const continuations = routeDirectionContinuations(route);

        for (const nextDirection of
            continuations.get(relationKey(state.direction)) || []) {
            if (
                !nextDirection.stops ||
                nextDirection.stops.length === 0
            ) {
                continue;
            }

            addRaptorOnboardState(nodeLabels, queue, {
                ...state,
                direction: nextDirection,
                pos: 0,
                currentLeg: continueRideLeg(
                    state.currentLeg,
                    nextDirection
                )
            });
        }
    }

    return evidence;
}

function bestRaptorTransferCandidates(byPath) {
    const byLastRoute = new Map();

    for (const candidate of flattenEvidence(byPath)) {
        const legs = candidateLegs(candidate);
        const lastLeg = legs[legs.length - 1];

        if (!lastLeg) {
            continue;
        }

        const routeKey = lastLeg.routeKey;
        let bucket = byLastRoute.get(routeKey) || [];

        if (bucket.some(item => itineraryDominates(item, candidate))) {
            continue;
        }

        bucket = bucket.filter(
            item => !itineraryDominates(candidate, item)
        );
        bucket.push(candidate);
        bucket.sort(compareItineraries);

        if (bucket.length > MAX_RAPTOR_TRANSFER_LABELS_PER_ROUTE) {
            bucket = bucket.slice(0, MAX_RAPTOR_TRANSFER_LABELS_PER_ROUTE);
        }

        byLastRoute.set(routeKey, bucket);
    }

    const result = [];

    for (const bucket of byLastRoute.values()) {
        result.push(...bucket);
    }

    result.sort(compareItineraries);
    return result.slice(0, MAX_RAPTOR_TRANSFER_LABELS_PER_STOP);
}

function buildRaptorTransferSources(evidence) {
    const result = new Map();

    for (const [alightStopIndex, byPath] of evidence.entries()) {
        const candidates = bestRaptorTransferCandidates(byPath);

        for (const candidate of candidates) {
            prepareItineraryMetrics(candidate);
            const legs = candidateLegs(candidate);
            const lastLeg = legs[legs.length - 1];

            if (!lastLeg) {
                continue;
            }

            for (const boardStopIndex of
                findNearbyStopsFromStop(alightStopIndex)) {
                const transferWalk = stopDistanceMeters(
                    alightStopIndex,
                    boardStopIndex
                );
                const transferMinutes =
                    transferWalk / WALK_SPEED_M_PER_MIN;
                const source = {
                    elapsedMinutes:
                        candidate.totalMinutes + transferMinutes,
                    walkMeters:
                        candidate.walkMeters + transferWalk,
                    initialWalk: candidate.initialWalk,
                    rideStops: candidate.rideStops,
                    transitMeters: candidate.transitMeters,
                    legs: legs,
                    transfers: candidateTransfers(candidate).concat([{
                        alightStopIndex: alightStopIndex,
                        boardStopIndex: boardStopIndex,
                        walkMeters: transferWalk
                    }]),
                    lastRouteKey: lastLeg.routeKey
                };

                addRaptorTransferSource(
                    result,
                    boardStopIndex,
                    source
                );
            }
        }
    }

    return result;
}

function createInitialRaptorSources(nearbyStops) {
    const result = new Map();

    for (const boardStopIndex of nearbyStops) {
        const initialWalk = pointToStopDistanceMeters(
            selectedPoint,
            boardStopIndex
        );

        addRaptorTransferSource(result, boardStopIndex, {
            elapsedMinutes: initialWalk / WALK_SPEED_M_PER_MIN,
            walkMeters: initialWalk,
            initialWalk: initialWalk,
            rideStops: 0,
            transitMeters: 0,
            legs: [],
            transfers: [],
            lastRouteKey: null
        });
    }

    return result;
}

function createReachabilitySearch() {
    const nearbyStops = findNearbyStops(
        selectedPoint.lat,
        selectedPoint.lon
    );

    return {
        nearbyStops: nearbyStops,
        evidence: [new Map(), new Map(), new Map()],
        boardSources: createInitialRaptorSources(nearbyStops),
        builtRounds: 0
    };
}

function ensureReachabilitySearch() {
    const cacheKey = reachabilitySearchKey();
    let context = reachabilitySearchCache.get(cacheKey);

    if (!context) {
        context = createReachabilitySearch();
        reachabilitySearchCache.set(cacheKey, context);

        if (reachabilitySearchCache.size > 4) {
            const firstKey = reachabilitySearchCache.keys().next().value;
            reachabilitySearchCache.delete(firstKey);
        }
    }

    const requiredRounds = Math.min(
        RAPTOR_ROUNDS,
        maxTransfers + 1
    );

    while (context.builtRounds < requiredRounds) {
        const round = context.builtRounds;

        if (!context.boardSources || context.boardSources.size === 0) {
            context.builtRounds = RAPTOR_ROUNDS;
            break;
        }

        context.evidence[round] = scanRaptorRound(
            context.boardSources,
            round
        );
        ++context.builtRounds;

        if (context.builtRounds < RAPTOR_ROUNDS) {
            context.boardSources = buildRaptorTransferSources(
                context.evidence[round]
            );
        }
    }

    return context;
}

function filterEvidenceByTravelLimit(evidence) {
    if (!Number.isFinite(maxTravelMinutes)) {
        return evidence;
    }

    const result = new Map();

    for (const [stopIndex, byPath] of evidence.entries()) {
        const filteredPaths = new Map();

        for (const [pathKey, bucket] of byPath.entries()) {
            const filtered = bucket.filter(
                candidate =>
                    candidate.totalMinutes <= maxTravelMinutes + 1e-7
            );

            if (filtered.length > 0) {
                filteredPaths.set(pathKey, filtered);
            }
        }

        if (filteredPaths.size > 0) {
            result.set(stopIndex, filteredPaths);
        }
    }

    return result;
}

function analyzeSelection() {
    const key = selectionKey();

    if (key === analysisCacheKey && analysisCache) {
        return analysisCache;
    }

    const search = ensureReachabilitySearch();
    const nearbyStops = search.nearbyStops;
    const directEvidence = filterEvidenceByTravelLimit(
        search.evidence[0]
    );
    const transferEvidence = maxTransfers >= 1
        ? filterEvidenceByTravelLimit(search.evidence[1])
        : new Map();
    const twoTransferEvidence = maxTransfers >= 2
        ? filterEvidenceByTravelLimit(search.evidence[2])
        : new Map();
    const directRoutes = new Set();
    const directDirectionIds = new Set();

    for (const paths of directEvidence.values()) {
        for (const path of flattenEvidence(paths)) {
            directRoutes.add(path.leg1.routeKey);
            directDirectionIds.add(relationKey(path.leg1.direction));
        }
    }

    const directStops = new Set(directEvidence.keys());
    const directTransitStops = new Set(directStops);
    const transferBoardingStops = new Set();
    const transferRoutes = new Set();
    const transferDirectionIds = new Set();
    const transferTransitStops = new Set(
        transferEvidence.keys()
    );
    const transferOnlyStops = new Set();

    for (const [stopIndex, paths] of transferEvidence.entries()) {
        if (!directStops.has(stopIndex)) {
            transferOnlyStops.add(stopIndex);
        }

        for (const path of flattenEvidence(paths)) {
            transferBoardingStops.add(path.transferBoardStopIndex);
            transferRoutes.add(path.leg2.routeKey);
            transferDirectionIds.add(
                relationKey(path.leg2.direction)
            );
        }
    }

    const twoTransferBoardingStops = new Set();
    const twoTransferRoutes = new Set();
    const twoTransferDirectionIds = new Set();
    const twoTransferTransitStops = new Set(
        twoTransferEvidence.keys()
    );
    const twoTransferOnlyStops = new Set();

    for (const [stopIndex, paths] of twoTransferEvidence.entries()) {
        if (
            !directStops.has(stopIndex) &&
            !transferEvidence.has(stopIndex)
        ) {
            twoTransferOnlyStops.add(stopIndex);
        }

        for (const path of flattenEvidence(paths)) {
            twoTransferBoardingStops.add(
                path.secondTransferBoardStopIndex
            );
            twoTransferRoutes.add(path.leg3.routeKey);
            twoTransferDirectionIds.add(
                relationKey(path.leg3.direction)
            );
        }
    }

    const directRouteData = collectReachableRouteData(
        directEvidence,
        "leg1"
    );
    const transferRouteData = collectReachableRouteData(
        transferEvidence,
        "leg2"
    );
    const twoTransferRouteData = collectReachableRouteData(
        twoTransferEvidence,
        "leg3"
    );
    const directArrivalMinutes = collectBestArrivalMinutes(
        directEvidence
    );
    const transferArrivalMinutes = collectBestArrivalMinutes(
        transferEvidence
    );
    const twoTransferArrivalMinutes = collectBestArrivalMinutes(
        twoTransferEvidence
    );

    analysisCacheKey = key;
    analysisCache = {
        nearbyStops: nearbyStops,
        directRoutes: directRoutes,
        directDirectionIds: directDirectionIds,
        directTransitStops: directTransitStops,
        directStops: directStops,
        directEvidence: directEvidence,
        directRouteSegments: directRouteData.segmentsByRoute,
        directRouteStops: directRouteData.stopsByRoute,
        directArrivalMinutes: directArrivalMinutes,
        transferBoardingStops: transferBoardingStops,
        transferRoutes: transferRoutes,
        transferDirectionIds: transferDirectionIds,
        transferTransitStops: transferTransitStops,
        transferOnlyStops: transferOnlyStops,
        transferEvidence: transferEvidence,
        transferRouteSegments: transferRouteData.segmentsByRoute,
        transferRouteStops: transferRouteData.stopsByRoute,
        transferArrivalMinutes: transferArrivalMinutes,
        twoTransferBoardingStops: twoTransferBoardingStops,
        twoTransferRoutes: twoTransferRoutes,
        twoTransferDirectionIds: twoTransferDirectionIds,
        twoTransferTransitStops: twoTransferTransitStops,
        twoTransferOnlyStops: twoTransferOnlyStops,
        twoTransferEvidence: twoTransferEvidence,
        twoTransferRouteSegments: twoTransferRouteData.segmentsByRoute,
        twoTransferRouteStops: twoTransferRouteData.stopsByRoute,
        twoTransferArrivalMinutes: twoTransferArrivalMinutes
    };

    return analysisCache;
}

function appendRouteGroup(container, title, routeKeys, transferCount) {
    if (routeKeys.size === 0) {
        return;
    }

    const heading = document.createElement("div");
    heading.className = "route-group-title";

    if (transferCount === 0) {
        heading.classList.add("route-group-title-direct");
    }

    heading.textContent = title;
    container.appendChild(heading);

    const ordered = Array.from(routeKeys);

    ordered.sort(
        (a, b) => {
            const ar = routes[a];
            const br = routes[b];
            const aa = ar && ar.ref ? ar.ref : "";
            const bb = br && br.ref ? br.ref : "";

            return aa.localeCompare(
                bb,
                undefined,
                {
                    numeric: true
                }
            );
        }
    );

    for (const key of ordered) {
        const route = routes[key];

        if (!route) {
            continue;
        }

        const item = document.createElement("span");
        item.className = "route-chip";
        item.style.background = routeColor(key);
        item.style.opacity = ["1", "0.65", "0.5"][transferCount];
        item.textContent = routeLabel(route);
        item.style.cursor = "pointer";

        if (
            selectedRouteFocus &&
            selectedRouteFocus.routeKey === key &&
            selectedRouteFocus.transferCount === transferCount
        ) {
            item.style.outline = "3px solid #111";
            item.style.outlineOffset = "2px";
        }

        item.addEventListener(
            "click",
            () => {
                const same =
                    selectedRouteFocus &&
                    selectedRouteFocus.routeKey === key &&
                    selectedRouteFocus.transferCount === transferCount;

                selectedRouteFocus = same
                    ? null
                    : { routeKey: key, transferCount: transferCount };
                renderSelection();
            }
        );

        container.appendChild(item);
    }
}

function focusedRouteStops(analysis) {
    if (!selectedRouteFocus) {
        return null;
    }

    const stopMaps = [
        analysis.directRouteStops,
        analysis.transferRouteStops,
        analysis.twoTransferRouteStops
    ];
    const routeStops = stopMaps[
        selectedRouteFocus.transferCount
    ].get(selectedRouteFocus.routeKey);

    if (!routeStops) {
        selectedRouteFocus = null;
        return null;
    }

    return routeStops;
}

function isFocusedRoute(routeKey, transferCount) {
    return (
        selectedRouteFocus &&
        selectedRouteFocus.routeKey === routeKey &&
        selectedRouteFocus.transferCount === transferCount
    );
}

function drawFocusedRouteStops(analysis) {
    const routeStops = focusedRouteStops(analysis);

    if (!routeStops || !selectedRouteFocus) {
        return;
    }

    const color = routeColor(selectedRouteFocus.routeKey);

    for (const stopIndex of routeStops) {
        const stop = stops[stopIndex];
        const marker = L.circleMarker(
            [stop.lat, stop.lon],
            {
                radius: 7,
                weight: 3,
                color: color,
                fillColor: "#fff",
                fillOpacity: 1,
                bubblingMouseEvents: false,
                pane: "stopPane"
            }
        );

        marker.bindPopup(
            "<b>" +
            escapeHtml(stop.name || TEXT.stop) +
            "</b><br>" +
            escapeHtml(routeLabel(routes[selectedRouteFocus.routeKey])) +
            "<br><span style='color:#666'>" +
            TEXT.doubleClick +
            "</span>"
        );

        marker.on(
            "click",
            event => {
                suppressMapClickUntil = performance.now() + 600;

                if (event.originalEvent) {
                    L.DomEvent.stop(event.originalEvent);
                }
            }
        );

        marker.on(
            "dblclick",
            event => {
                suppressMapClickUntil = performance.now() + 600;

                if (event.originalEvent) {
                    L.DomEvent.stop(event.originalEvent);
                }

                marker.closePopup();
                showTripToStop(stopIndex);
            }
        );

        marker.addTo(stopLayer);
    }
}

function updateRouteList(
    directRoutes,
    transferRoutes,
    twoTransferRoutes
) {
    const container = document.getElementById("routes");
    container.innerHTML = "";

    appendRouteGroup(
        container,
        TEXT.groupDirect,
        directRoutes,
        0
    );

    if (maxTransfers >= 1) {
        appendRouteGroup(
            container,
            TEXT.groupTransfer,
            transferRoutes,
            1
        );
    }

    if (maxTransfers >= 2) {
        appendRouteGroup(
            container,
            TEXT.groupTwoTransfers,
            twoTransferRoutes,
            2
        );
    }
}

function renderSelection() {
    clearResult();
    updateControls();

    if (!selectedPoint) {
        return;
    }

    analysisSummary.hidden = false;
    tripPanel.hidden = true;

    drawOrigin(selectedPoint.lat, selectedPoint.lon);

    const analysis = analyzeSelection();
    const focusStops = focusedRouteStops(analysis);
    const hasFocus = !!selectedRouteFocus && !!focusStops;

    if (maxTransfers >= 2) {
        for (const routeKey of analysis.twoTransferRoutes) {
            if (hasFocus && !isFocusedRoute(routeKey, 2)) {
                continue;
            }

            drawRoute(
                routeKey,
                2,
                analysis.twoTransferRouteSegments.get(routeKey)
            );
        }

        if (!hasFocus) {
            for (const stopIndex of analysis.twoTransferOnlyStops) {
                drawCoverage(
                    stops[stopIndex],
                    2,
                    analysis.twoTransferArrivalMinutes.get(stopIndex)
                );
                drawStop(stopIndex, 2);
            }
        }
    }

    if (maxTransfers >= 1) {
        for (const routeKey of analysis.transferRoutes) {
            if (hasFocus && !isFocusedRoute(routeKey, 1)) {
                continue;
            }

            drawRoute(
                routeKey,
                1,
                analysis.transferRouteSegments.get(routeKey)
            );
        }

        if (!hasFocus) {
            for (const stopIndex of analysis.transferOnlyStops) {
                drawCoverage(
                    stops[stopIndex],
                    1,
                    analysis.transferArrivalMinutes.get(stopIndex)
                );
                drawStop(stopIndex, 1);
            }
        }
    }

    for (const routeKey of analysis.directRoutes) {
        if (hasFocus && !isFocusedRoute(routeKey, 0)) {
            continue;
        }

        drawRoute(
            routeKey,
            0,
            analysis.directRouteSegments.get(routeKey)
        );
    }

    if (!hasFocus) {
        for (const stopIndex of analysis.directStops) {
            drawCoverage(
                stops[stopIndex],
                0,
                analysis.directArrivalMinutes.get(stopIndex)
            );
            drawStop(stopIndex, 0);
        }
    }

    if (!hasFocus) {
        flushCoverage();
    }

    document.getElementById(
        "nearby-count"
    ).textContent = analysis.nearbyStops.length;

    document.getElementById(
        "direct-route-count"
    ).textContent = analysis.directRoutes.size;

    document.getElementById(
        "direct-stop-count"
    ).textContent = analysis.directStops.size;

    document.getElementById(
        "transfer-route-count"
    ).textContent = maxTransfers >= 1
        ? analysis.transferRoutes.size
        : 0;

    document.getElementById(
        "transfer-stop-count"
    ).textContent = maxTransfers >= 1
        ? analysis.transferOnlyStops.size
        : 0;

    document.getElementById(
        "two-transfer-route-count"
    ).textContent = maxTransfers >= 2
        ? analysis.twoTransferRoutes.size
        : 0;

    document.getElementById(
        "two-transfer-stop-count"
    ).textContent = maxTransfers >= 2
        ? analysis.twoTransferOnlyStops.size
        : 0;

    if (hasFocus) {
        drawFocusedRouteStops(analysis);
    }

    updateRouteList(
        analysis.directRoutes,
        analysis.transferRoutes,
        analysis.twoTransferRoutes
    );
}

function scheduleRender() {
    updateControls();

    if (!selectedPoint) {
        return;
    }

    if (renderFrame !== null) {
        cancelAnimationFrame(renderFrame);
    }

    renderFrame = requestAnimationFrame(
        () => {
            renderFrame = null;

            if (targetPoint) {
                renderTrip(targetPoint);
            } else {
                renderSelection();
            }
        }
    );
}

for (const input of modeInputs) {
    input.addEventListener("change", () => {
        enabledModes = new Set(
            modeInputs.filter(item => item.checked).map(item => item.value)
        );
        nearbyStopCache.clear();
        boardingOptionsCache.clear();
        reachabilitySearchCache.clear();
        itineraryCache.clear();
        analysisCacheKey = null;
        analysisCache = null;
        selectedRouteFocus = null;
        tripAlternativeIndex = 0;
        scheduleRender();
    });
}

walkTimeInput.addEventListener(
    "input",
    scheduleRender
);

transferCountInput.addEventListener(
    "input",
    scheduleRender
);

travelTimeLimitInput.addEventListener(
    "input",
    () => {
        updateControls();

        if (travelRenderTimer !== null) {
            clearTimeout(travelRenderTimer);
        }

        travelRenderTimer = setTimeout(
            () => {
                travelRenderTimer = null;
                scheduleRender();
            },
            70
        );
    }
);

map.on("zoomend", () => {
    redrawCoverageOverlay();
    updateStopMarkerSizes();
});

map.on("resize", () => {
    redrawCoverageOverlay();
});

circleOpacityInput.addEventListener(
    "input",
    () => {
        circleOpacity = Number(circleOpacityInput.value) / 100.0;
        circleOpacityValue.textContent =
            Math.round(circleOpacity * 100) + "%";
        coverageGroup.setAttribute("opacity", String(circleOpacity));
    }
);

tripBack.addEventListener(
    "click",
    () => {
        targetPoint = null;
        setTargetPickMode(false);
        tripAlternativeIndex = 0;
        renderSelection();
    }
);

function selectStartPoint(latlng) {
    selectedPoint = {
        lat: latlng.lat,
        lon: latlng.lng
    };
    targetPoint = null;
    setTargetPickMode(false);
    tripAlternativeIndex = 0;
    selectedRouteFocus = null;

    analysisCacheKey = null;
    analysisCache = null;
    reachabilitySearchCache.clear();
    itineraryCache.clear();
    renderSelection();
}

function cancelPendingMapClick() {
    if (pendingMapClickTimer === null) {
        return;
    }

    clearTimeout(pendingMapClickTimer);
    pendingMapClickTimer = null;
}

map.on(
    "click",
    event => {
        if (targetPickMode && selectedPoint) {
            cancelPendingMapClick();
            suppressMapClickUntil = performance.now() + 600;
            showTripToPoint({
                lat: event.latlng.lat,
                lon: event.latlng.lng
            });
            return;
        }

        const stopIndex = nearestDisplayedStop(
            event.latlng,
            14
        );

        if (stopIndex !== null) {
            cancelPendingMapClick();
            suppressMapClickUntil = performance.now() + 600;
            openStopPopup(stopIndex);
            return;
        }

        if (performance.now() < suppressMapClickUntil) {
            return;
        }

        if (event.originalEvent && event.originalEvent._stopped) {
            return;
        }

        const latlng = {
            lat: event.latlng.lat,
            lng: event.latlng.lng
        };

        cancelPendingMapClick();
        pendingMapClickTimer = setTimeout(
            () => {
                pendingMapClickTimer = null;
                selectStartPoint(latlng);
            },
            MAP_DOUBLE_CLICK_DELAY_MS
        );
    }
);

map.on(
    "dblclick",
    event => {
        cancelPendingMapClick();
        suppressMapClickUntil = performance.now() + 600;

        if (event.originalEvent) {
            L.DomEvent.stop(event.originalEvent);
        }

        if (!selectedPoint) {
            selectStartPoint(event.latlng);
            return;
        }

        tripAlternativeIndex = 0;
        showTripToPoint({
            lat: event.latlng.lat,
            lon: event.latlng.lng
        });
    }
);

updateControls();
})().catch(error => {
    console.error(error);
    alert("Failed to initialize transport map: " + error.message);
});
