        const FTP = __FTP__;
        const CTL_TODAY = __CTL_TODAY__;
        const ATL_TODAY = __ATL_TODAY__;
        const CTL_DECAY = Math.exp(-1 / 42);
        const ATL_DECAY = Math.exp(-1 / 7);

        const simLow = document.getElementById("simLow");
        const optimalWindowBox = document.getElementById("optimalWindowBox");

        // Wettervorhersage fuer die naechsten 7 Tage (Index 0 = Tag 1 / morgen).
        const WEATHER_FORECAST = __WEATHER_FORECAST_JSON__;

        // Routen-Planer: Karten zentriert auf den Linzer Hauptplatz.
        const LINZ_HAUPTPLATZ = [48.3061, 14.2861];
        const ROUTE_DESTINATIONS = [
            { name: "Traun", distance: 10 },
            { name: "Ottensheim", distance: 10 },
            { name: "Gallneukirchen", distance: 10 },
            { name: "Enns", distance: 20 },
            { name: "Eferding", distance: 20 },
            { name: "Pregarten", distance: 20 },
            { name: "Wels", distance: 30 },
            { name: "Bad Leonfelden", distance: 30 },
            { name: "Amstetten", distance: 30 },
            { name: "Freistadt", distance: 40 },
            { name: "Steyr", distance: 45 },
            { name: "Gmunden", distance: 50 },
            { name: "Passau", distance: 55 },
        ];

        // OpenRouteService: API-Key fuer echte Routen- und Hoehenmeter-Abfragen.
        const ORS_API_KEY = "__ORS_API_KEY__";

        // Vier Richtungs-Ziele rund um Linz, je nach Wochentag rotierend gewaehlt.
        const ROUTE_DIRECTIONS = [
            { name: "Bad Leonfelden", coords: [48.5167, 14.2833] },  // Norden / Muehlviertel
            { name: "Enns", coords: [48.2192, 14.4836] },            // Osten
            { name: "Neuhofen an der Krems", coords: [48.1667, 14.2333] },  // Sueden
            { name: "Eferding", coords: [48.3094, 13.8531] },        // Westen
        ];

        const routeMaps = {};
        const routeCircles = {};
        const routeLayers = {};
        const routeLoaded = {};
        const routeLoading = {};
        const routeReloadTimers = {};

        // Die Karten werden erst beim ersten Aufklappen des Routen-Planer-
        // Accordions erzeugt, da Leaflet eine sichtbare Container-Groesse
        // benoetigt, um sich korrekt zu initialisieren.
        function initRouteMap(day) {
            if (routeMaps[day]) {
                return routeMaps[day];
            }

            const map = L.map("map" + day, {
                zoomControl: false,
                attributionControl: false,
                dragging: false,
                scrollWheelZoom: false,
                doubleClickZoom: false,
            }).setView(LINZ_HAUPTPLATZ, 10);

            L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
                subdomains: "abcd",
                maxZoom: 19,
            }).addTo(map);

            L.marker(LINZ_HAUPTPLATZ).addTo(map);

            const circle = L.circle(LINZ_HAUPTPLATZ, {
                radius: 1,
                color: "#39ff14",
                weight: 1.5,
                fillColor: "#39ff14",
                fillOpacity: 0.1,
            }).addTo(map);

            routeMaps[day] = map;
            routeCircles[day] = circle;

            const minutes = parseInt(document.querySelector(`.day-duration[data-day="${day}"]`).value, 10);
            const watts = parseInt(document.querySelector(`.day-power[data-day="${day}"]`).value, 10);
            const radiusKm = estimateRouteDistance(minutes, watts) / 2;
            circle.setRadius(Math.max(radiusKm * 1000, 1));
            map.fitBounds(circle.getBounds());

            return map;
        }

        document.querySelectorAll(".route-planner").forEach(details => {
            details.addEventListener("toggle", () => {
                if (!details.open) {
                    return;
                }
                const day = details.dataset.day;
                const map = initRouteMap(day);
                map.invalidateSize();
                const circle = routeCircles[day];
                if (circle) {
                    map.fitBounds(circle.getBounds());
                } else if (routeLayers[day]) {
                    map.fitBounds(routeLayers[day].getBounds());
                }
            });
        });

        function estimateRouteDistance(minutes, watts) {
            const speedKmh = 22 + (watts / FTP) * 11;
            return (minutes / 60) * speedKmh;
        }

        function updateRoutePlanner(day, minutes, watts) {
            const distance = estimateRouteDistance(minutes, watts);
            document.getElementById("distanceValue" + day).textContent = distance.toFixed(1) + " km";

            const radius = distance / 2;
            const waypoints = ROUTE_DESTINATIONS
                .map(dest => ({ name: dest.name, diff: Math.abs(dest.distance - radius) }))
                .sort((a, b) => a.diff - b.diff)
                .slice(0, 3)
                .map(dest => dest.name)
                .join(", ");

            document.getElementById("waypointsValue" + day).textContent =
                `${waypoints} (Wendepunkt ca. ${radius.toFixed(1)} km)`;

            const circle = routeCircles[day];
            const map = routeMaps[day];
            if (circle) {
                const radiusMeters = Math.max(radius * 1000, 1);
                circle.setRadius(radiusMeters);
                map.fitBounds(circle.getBounds());
            }
        }

        function haversineKm(a, b) {
            const R = 6371;
            const dLat = (b[0] - a[0]) * Math.PI / 180;
            const dLon = (b[1] - a[1]) * Math.PI / 180;
            const lat1 = a[0] * Math.PI / 180;
            const lat2 = b[0] * Math.PI / 180;
            const sinDLat = Math.sin(dLat / 2);
            const sinDLon = Math.sin(dLon / 2);
            const c = sinDLat * sinDLat + Math.cos(lat1) * Math.cos(lat2) * sinDLon * sinDLon;
            return 2 * R * Math.asin(Math.sqrt(c));
        }

        function interpolateTowards(start, end, distanceKm) {
            const totalKm = haversineKm(start, end);
            const fraction = totalKm > 0 ? Math.min(Math.max(distanceKm / totalKm, 0), 1) : 0;
            return [
                start[0] + (end[0] - start[0]) * fraction,
                start[1] + (end[1] - start[1]) * fraction,
            ];
        }

        async function loadRealRoute(day) {
            if (routeLoading[day]) {
                return;
            }
            routeLoading[day] = true;

            const btn = document.getElementById("routeBtn" + day);
            const elevationEl = document.getElementById("elevationValue" + day);
            const targetEl = document.getElementById("routeTargetValue" + day);
            const minutes = parseInt(document.querySelector(`.day-duration[data-day="${day}"]`).value, 10);
            const watts = parseInt(document.querySelector(`.day-power[data-day="${day}"]`).value, 10);
            const radiusKm = estimateRouteDistance(minutes, watts) / 2;

            const target = ROUTE_DIRECTIONS[(day - 1) % ROUTE_DIRECTIONS.length];
            targetEl.textContent = `📍 Ziel: ${target.name}`;
            const waypoint = interpolateTowards(LINZ_HAUPTPLATZ, target.coords, radiusKm);

            btn.disabled = true;
            btn.textContent = "Lade Route...";

            try {
                const response = await fetch("https://api.openrouteservice.org/v2/directions/cycling-road/geojson", {
                    method: "POST",
                    headers: {
                        "Authorization": ORS_API_KEY,
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({
                        coordinates: [
                            [LINZ_HAUPTPLATZ[1], LINZ_HAUPTPLATZ[0]],
                            [waypoint[1], waypoint[0]],
                        ],
                        elevation: true,
                    }),
                });

                if (!response.ok) {
                    throw new Error("ORS-Antwort: " + response.status);
                }

                const geojson = await response.json();
                const properties = geojson.features[0].properties;
                const ascent = properties.ascent ?? properties.summary.ascent;
                elevationEl.textContent = Math.round(ascent) + " hm";

                const map = initRouteMap(day);
                if (routeCircles[day]) {
                    map.removeLayer(routeCircles[day]);
                    routeCircles[day] = null;
                }
                if (routeLayers[day]) {
                    map.removeLayer(routeLayers[day]);
                }

                const layer = L.geoJSON(geojson, {
                    style: { color: "#39ff14", weight: 4, opacity: 0.85 },
                }).addTo(map);
                routeLayers[day] = layer;
                map.fitBounds(layer.getBounds());

                routeLoaded[day] = true;
                btn.textContent = "🗺️ Echte Route geladen";
                btn.disabled = false;
            } catch (err) {
                console.error(err);
                elevationEl.textContent = "Fehler";
                btn.textContent = "🗺️ Erneut versuchen";
                btn.disabled = false;
            } finally {
                routeLoading[day] = false;
            }
        }

        // Das Ausblenden einer Tages-Karte dient nur dem Layout - die Werte
        // ihrer Slider bleiben weiterhin Teil der Formkurven-Simulation.
        function setDayCardVisible(weekday, visible) {
            document.querySelectorAll(`.day-card[data-weekday="${weekday}"]`).forEach(card => {
                if (visible) {
                    card.classList.remove("day-card-fading");
                    card.classList.remove("day-card-hidden");
                } else {
                    card.classList.add("day-card-fading");
                    setTimeout(() => card.classList.add("day-card-hidden"), 200);
                }
            });
        }

        document.querySelectorAll(".filter-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                btn.classList.toggle("active");
                setDayCardVisible(btn.dataset.weekday, btn.classList.contains("active"));
            });
        });

        const WEEKDAYS = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];

        function formatDate(daysFromToday) {
            const d = new Date();
            d.setDate(d.getDate() + daysFromToday);
            return WEEKDAYS[d.getDay()] + ", " + d.toLocaleDateString("de-DE");
        }

        function isoDate(daysFromToday) {
            const d = new Date();
            d.setDate(d.getDate() + daysFromToday);
            return d.toISOString().split("T")[0];
        }

        function updateOptimalWindow(forecastTsb, ctlEnd, atlEnd) {
            if (forecastTsb[0] >= 0) {
                optimalWindowBox.innerHTML = "<strong>🔥 Du bist absolut frisch! Zeit fuer das naechste Training!! 🔥</strong>";
                optimalWindowBox.classList.add("fresh");
                return;
            }
            optimalWindowBox.classList.remove("fresh");

            for (let day = 1; day <= 7; day++) {
                if (forecastTsb[day] >= 0) {
                    optimalWindowBox.innerHTML = "Mit deiner aktuellen Planung erreichst du deine optimale Frische am <strong>" + formatDate(day) + "</strong>.";
                    return;
                }
            }

            let ctl = ctlEnd;
            let atl = atlEnd;
            for (let day = 8; day <= 14; day++) {
                ctl = ctl * CTL_DECAY;
                atl = atl * ATL_DECAY;
                if (ctl - atl >= 0) {
                    optimalWindowBox.innerHTML = "Mit deiner aktuellen Planung erreichst du deine optimale Frische am <strong>" + formatDate(day) + "</strong>.";
                    return;
                }
            }

            optimalWindowBox.innerHTML = "Mit deiner aktuellen Planung liegt deine optimale Frische mehr als 14 Tage in der Zukunft.";
        }

        function scoreWeatherDay(weather) {
            let score = 100;
            score -= weather.precip_prob * 1.5;
            score -= Math.abs(weather.temp_max - 19) * 2;
            score -= weather.wind_speed > 25 ? 15 : weather.wind_speed * 0.3;
            return score;
        }

        function updateWeatherOracle(forecastTsb) {
            const daysEl = document.getElementById("weatherOracleDays");
            const tipEl = document.getElementById("weatherOracleTip");

            if (!WEATHER_FORECAST || WEATHER_FORECAST.length === 0) {
                daysEl.textContent = "Keine Wetterdaten verfuegbar.";
                tipEl.textContent = "";
                return;
            }

            const ranked = WEATHER_FORECAST.map((weather, idx) => ({
                day: idx + 1,
                weather: weather,
                score: scoreWeatherDay(weather),
            })).sort((a, b) => b.score - a.score);

            const top3 = ranked.slice(0, 3);
            daysEl.textContent = top3
                .map((entry, i) => `${i + 1}. ${formatDate(entry.day).split(",")[0]}`)
                .join(" | ");

            const best = top3[0];
            const bestLabel = formatDate(best.day).split(",")[0];
            const bestTsb = forecastTsb[best.day];

            if (bestTsb >= 0) {
                tipEl.textContent = `Nutze das Kaiserwetter am ${bestLabel} fuer deine Koenigseinheit!`;
            } else if (bestTsb >= -10) {
                tipEl.textContent = `${bestLabel} bietet die besten Bedingungen - eine solide Einheit ist gut machbar.`;
            } else {
                tipEl.textContent = `${bestLabel} hat das beste Wetter, aber deine Form ist noch angespannt - plane lieber locker.`;
            }
        }

        function simulate() {
            let ctl = CTL_TODAY;
            let atl = ATL_TODAY;

            const forecastDates = [isoDate(0)];
            const forecastCtl = [ctl];
            const forecastAtl = [atl];
            const forecastTsb = [ctl - atl];

            let lowestTsb = forecastTsb[0];
            let lowestDay = 0;

            for (let day = 1; day <= 7; day++) {
                const durationSlider = document.querySelector(`.day-duration[data-day="${day}"]`);
                const powerSlider = document.querySelector(`.day-power[data-day="${day}"]`);
                const minutes = parseInt(durationSlider.value, 10);
                const watts = parseInt(powerSlider.value, 10);

                document.getElementById("durationValue" + day).textContent = minutes + " min";
                document.getElementById("powerValue" + day).textContent = watts + " W";

                const intensityFactor = watts / FTP;
                const durationSeconds = minutes * 60;
                const dayTss = (durationSeconds * watts * intensityFactor) / (FTP * 3600) * 100;
                document.getElementById("tssValue" + day).textContent = dayTss.toFixed(1);

                updateRoutePlanner(day, minutes, watts);

                if (routeLoaded[day]) {
                    document.getElementById("routeBtn" + day).textContent = "Lade neue Route...";
                    clearTimeout(routeReloadTimers[day]);
                    routeReloadTimers[day] = setTimeout(() => loadRealRoute(day), 1200);
                }

                ctl = ctl * CTL_DECAY + dayTss * (1 - CTL_DECAY);
                atl = atl * ATL_DECAY + dayTss * (1 - ATL_DECAY);
                const tsb = ctl - atl;

                forecastDates.push(isoDate(day));
                forecastCtl.push(ctl);
                forecastAtl.push(atl);
                forecastTsb.push(tsb);

                if (tsb < lowestTsb) {
                    lowestTsb = tsb;
                    lowestDay = day;
                }
            }

            simLow.textContent = lowestTsb.toFixed(1) + " (" + formatDate(lowestDay) + ")";

            Plotly.restyle("formkurve-chart", {
                x: [forecastDates, forecastDates, forecastDates],
                y: [forecastCtl, forecastAtl, forecastTsb],
            }, [6, 7, 8]);

            updateOptimalWindow(forecastTsb, ctl, atl);
            updateWeatherOracle(forecastTsb);
        }

        document.querySelectorAll(".day-duration, .day-power").forEach(slider => {
            slider.addEventListener("input", simulate);
        });

        simulate();
