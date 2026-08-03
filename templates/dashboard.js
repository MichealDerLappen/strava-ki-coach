        function switchSport(val) {
            document.getElementById("cycling-content").style.display = val === "cycling" ? "block" : "none";
            document.getElementById("hiking-content").style.display  = val === "hiking"  ? "block" : "none";
        }

        const FTP = __FTP__;
        const CTL_TODAY = __CTL_TODAY__;
        const ATL_TODAY = __ATL_TODAY__;
        const CTL_DECAY = Math.exp(-1 / 42);
        const ATL_DECAY = Math.exp(-1 / 7);

        const simLow = document.getElementById("simLow");
        const optimalWindowBox = document.getElementById("optimalWindowBox");

        // Wettervorhersage fuer die naechsten 7 Tage (Index 0 = Tag 1 / morgen).
        const WEATHER_FORECAST = __WEATHER_FORECAST_JSON__;

        // Trainingstyp-Empfehlung pro Tag basierend auf TSB-Prognose,
        // Wochentag (0=Mo … 6=So) und Wetter.
        function suggestTraining(day, tsb, weekday, weather) {
            const rain   = weather ? weather.precip_prob : 0;
            const wind   = weather ? weather.wind_speed  : 0;
            const indoor = rain > 60;

            let type, color, icon, detail;

            if (tsb < -25) {
                type   = "Ruhetag";
                icon   = "😴";
                color  = "#e74c3c";
                detail = "Körper erholen lassen – kein Training heute.";
            } else if (tsb < -12) {
                type   = indoor ? "Lockeres Indoor" : "Grundlage locker";
                icon   = indoor ? "🏠" : "🟢";
                color  = "#2ecc71";
                detail = indoor
                    ? "Regen – Rolle oder Kraft statt Straße."
                    : "Z1-Fahrt, niedrige HF, kein Druck.";
            } else if (tsb < 0) {
                if (weekday === 4 /* Fr */ || weekday === 5 /* Sa */ ) {
                    type   = indoor ? "Sweet Spot Indoor" : "Sweet Spot";
                    icon   = "🎯";
                    color  = "#f1c40f";
                    detail = "Moderate Intensität, 88–93 % FTP, kontrolliert.";
                } else {
                    type   = indoor ? "Tempo Indoor" : "Tempo";
                    icon   = "⚡";
                    color  = "#f1c40f";
                    detail = "Schwellennahe Arbeit, 95–105 % FTP.";
                }
            } else if (tsb < 12) {
                if (weekday === 5 /* Sa */ || weekday === 6 /* So */) {
                    type   = indoor ? "Sweet Spot Indoor" : "Long Ride";
                    icon   = indoor ? "🎯" : "🚴";
                    color  = "#3498db";
                    detail = indoor
                        ? "Wetter schlecht – lieber Rolle."
                        : "Langer Ausdauer-Ride, ruhiges Tempo, Umfang nutzen.";
                } else {
                    type   = wind > 25 ? "Intervalle (Gegenwind nutzen)" : "Intervalle";
                    icon   = "🔥";
                    color  = "#e67e22";
                    detail = "VO2max oder anaerobe Intervalle, kurze Pausen.";
                }
            } else {
                if (weekday === 5 || weekday === 6) {
                    type   = indoor ? "Sweet Spot Indoor" : "Long Ride / Renntempo";
                    icon   = indoor ? "🎯" : "🏆";
                    color  = "#3498db";
                    detail = indoor
                        ? "Zu frisch für drinnen – aber Regen lässt keine Wahl."
                        : "Top-Form, Renntempo oder langer Ride mit Segmenten.";
                } else {
                    type   = "Intervalle – du bist frisch";
                    icon   = "🔥";
                    color  = "#e67e22";
                    detail = "Beste Form für harte Einheiten – nutze die Frische.";
                }
            }

            const el = document.getElementById("trainingSuggestion" + day);
            if (el) {
                el.innerHTML = `
                    <span class="ts-badge" style="border-color:${color};color:${color};">${icon} ${type}</span>
                    <span class="ts-detail">${detail}</span>`;
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

                ctl = ctl * CTL_DECAY + dayTss * (1 - CTL_DECAY);
                atl = atl * ATL_DECAY + dayTss * (1 - ATL_DECAY);
                const tsb = ctl - atl;

                const d = new Date();
                d.setDate(d.getDate() + day);
                const weekday = (d.getDay() + 6) % 7; // 0=Mo … 6=So
                const weather = WEATHER_FORECAST[day - 1] || null;
                suggestTraining(day, tsb, weekday, weather);

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

        simulate();

        function toggleHeatLayer(iframeId, name, btn) {
            btn.classList.toggle('active');
            const visible = btn.classList.contains('active');
            const iframe = document.getElementById(iframeId);
            if (iframe && iframe.contentWindow)
                iframe.contentWindow.postMessage({type: 'setLayerVisible', name: name, visible: visible}, '*');
        }
