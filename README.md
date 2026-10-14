# Moscow Transport Reachability Map

Moscow Transport Reachability Map is an interactive browser map for exploring public-transport
accessibility in Moscow using OpenStreetMap data.

`build-transport-map.py` downloads and preprocesses OSM data, extracts Moscow bus, tram,
trolleybus, metro, MCC and MCD routes and stops, and generates `routes.html` with the
transport data embedded in the page. Leaflet, MapLibre GL and the OpenFreeMap vector
background are loaded from the network.
OpenFreeMap needs no API key. The only map background is Bright, with no style selector.
The updated OSM extraction preserves child routes of route_master relations and attempts to
recover railway stations omitted from route stops by snapping mapped stations to rail routes.
If the MCC toggle is enabled, its entire available line is shown as a thin brown
reference line, independently of the colored reachable route sections. If an OSM
route segment cannot be matched to railway geometry, the browser renders an
approximate station-to-station segment rather than hiding it.
When MCC route relations are incomplete, the generator can build an approximate ring from
at least 24 named MCC station objects found in the local OSM extract. This station-to-station
fallback is not the actual railway track geometry.

The page can calculate approximate reachable areas with zero, one, or two transfers and can
build route variants to a selected destination. It is designed for exploratory accessibility
analysis rather than exact trip planning: walking distance is approximated geometrically and
vehicle travel time is estimated from the transport graph rather than a live timetable.

## Описание

Moscow Transport Reachability Map - интерактивная браузерная карта для исследования
транспортной доступности в Москве на основе данных OpenStreetMap.

`build-transport-map.py` загружает и предварительно обрабатывает данные OSM, извлекает
московские маршруты и остановки автобусов, трамваев, троллейбусов, метро, МЦК и МЦД и
генерирует `routes.html` со встроенными транспортными данными. Библиотеки Leaflet,
MapLibre GL и векторная подложка Bright от OpenFreeMap загружаются из сети.
Переключателя стилей нет. Пропущенные станции метро и железнодорожных линий дополнительно
ищутся среди самостоятельных объектов OSM и привязываются к геометрии маршрутов.
При включённом МЦК показывается тонкая коричневая линия всей найденной сети,
независимо от цветных достижимых участков. Если геометрия OSM не подходит для
отрисовки, участок соединяется по координатам последовательных станций.
Если отношения МЦК неполные, из станций OSM может строиться приблизительное кольцо.
Это соединение станций прямыми сегментами, а не точная геометрия железной дороги.

Страница может рассчитывать приблизительные зоны доступности без пересадок, с одной или двумя
пересадками, а также строить варианты маршрута до выбранной точки. Проект предназначен для
исследовательского анализа доступности, а не для точного планирования поездок: расстояние
пешком оценивается геометрически, а время движения транспорта рассчитывается по транспортному
графу, а не по актуальному расписанию.

## Готовая версия

Уже собранная версия карты доступна по адресу:

https://bus-routing.e7s.site/

Версия на сайте может немного отставать от текущей версии в репозитории.

## Возможности

- автобус, трамвай, троллейбус, метро, МЦК и МЦД;
- отдельные кнопки-переключатели с SVG-иконками для каждого вида транспорта;
- по умолчанию включены только автобус, трамвай и троллейбус;
- фильтры одновременно влияют на отображение, доступность, пересадки и поиск поездок;
- отображение маршрутов и остановок;
- расчет доступности от произвольной точки;
- 0, 1 или 2 пересадки;
- ограничение общего времени поездки;
- настройка допустимого времени пешком;
- построение маршрута до произвольной точки;
- несколько вариантов маршрута;
- подложка OpenFreeMap Bright без переключателя стилей;
- отдельная тонкая линия МЦК при включенном режиме МЦК;
- восстановление недоступных для отрисовки участков по станциям;
- восстановление части отсутствующих станций по отдельным объектам OSM;
- резервное построение МЦК по 24-31 станциям OSM при неполных маршрутах;
- отчет о найденных станциях МЦК и предупреждение об упрощенной геометрии;
- адаптивный интерфейс для desktop/mobile.

Расстояние пешком рассчитывается по прямой, а время движения транспорта является оценочным,
поэтому карта предназначена прежде всего для анализа транспортной доступности, а не для
точного планирования поездок.

## Требования

- Python 3
- Python-модуль `osmium`

```bash
pip install osmium
```

## Запуск

```bash
python3 build-transport-map.py
```

При первом запуске скрипт скачает PBF Центрального федерального округа с Geofabrik, создаст
транспортный кеш и сгенерирует:

```text
routes.html
cache/
  central-fed-district.osm.pbf
  moscow-transport-v4.osm.pbf
```

После этого достаточно открыть `routes.html` в браузере. Для отображения подложки требуется
доступ к OpenFreeMap и библиотекам на unpkg.com. Данные транспорта встроены в HTML.

Если подложка не отображается при `file://`, можно открыть файл через локальный сервер:

```bash
python3 -m http.server 8000
```

Затем открыть `http://localhost:8000/routes.html`.

Подложка карты теперь использует OpenFreeMap (векторные тайлы), а не публичные растровые тайлы
`tile.openstreetmap.org`. Это устраняет зависимость от сервера, показывающего `Access blocked`.
OSM по-прежнему используется как источник транспортных и картографических данных.
Подложка является отдельным слоем и не влияет на расчёты транспортной доступности.

## Использование карты

Один клик по карте выбирает начальную точку и показывает доступную транспортную сеть.

Подложка Bright используется постоянно, переключать стиль на сайте не нужно.

В панели можно изменить:

- виды транспорта: автобус, трамвай, троллейбус, метро, МЦК, МЦД (любая комбинация);
- максимальное время пешком;
- количество пересадок: 0-2;
- максимальное время поездки;
- прозрачность зон доступности.

Двойной клик по карте строит маршрут от выбранной начальной точки до указанного места.

## Обновление данных

Скачать свежий OSM PBF и полностью обновить кеш:

```bash
python3 build-transport-map.py --update
```

Пересобрать транспортные данные из уже скачанного PBF:

```bash
python3 build-transport-map.py --rebuild
```

Обычный запуск повторно использует существующий кеш. В версии 0.2.3 имя транспортного кеша
изменено на `moscow-transport-v4.osm.pbf` для принудительной пересборки транспорта по
обновлённым правилам OSM. Исходный `central-fed-district.osm.pbf` скачивать повторно не нужно,
если он уже есть. Для действительно свежего источника используйте `--update`.

## Модель скорости и ограничения

Средние скорости заданы как **приблизительные скорости движения** по геометрии маршрута. К ним
дополнительно прибавляются 0,35 мин на остановку и оценка ожидания при каждой посадке:

| Вид транспорта | Скорость движения | Ожидание посадки |
| --- | ---: | ---: |
| Автобус | 19,2 км/ч | 4 мин |
| Трамвай | 18 км/ч | 4 мин |
| Троллейбус | 16,8 км/ч | 4 мин |
| Метро | 37,8 км/ч | 2,5 мин |
| МЦК | 46,8 км/ч | 4 мин |
| МЦД | 54 км/ч | 5 мин |

Эти значения - эвристики для карты, не статистика по фактическим рейсам. Время пересадок
зависит от геометрического расстояния между остановками, а не от реальных переходов по
станциям. У входов в метро, на платформах и при пересадках сквозь крупные станции это может
давать заметную ошибку. Нет расписаний, расчёта по дорожному графу для пешеходов, интервалов
движения или учёта платных зон.

Данные метро выбираются по OSM `route=subway` (и московскому `light_rail`), МЦК и МЦД - по
признакам линий на железнодорожных `route=train` / `route_master=train`, включая `ref`, `name`
и `network`. Дочерние `route` наследуют классификацию от выбранного `route_master`.
Дополнительно извлекаются объекты `railway=station/halt` в московском регионе, отсутствующие
в списках остановок отношений. Для железнодорожных маршрутов станция добавляется к маршруту,
если она близка к его геометрии и не дублирует имеющуюся остановку (расстояние до 100 м).
Такая привязка является эвристикой и может требовать проверки в местах пересечения линий.
Если маршрут OSM не содержит путей, линия приблизительно соединяет последовательные остановки.

Генератор выводит число направлений каждого вида транспорта, а также количество найденных
объектов станции `Шаболовская`. Если станция всё ещё не найдена, выведется предупреждение.
Включённые режимы используют только маршруты, найденные в OSM.

## Основные параметры

```text
-o, --output FILE       имя выходного HTML (routes.html)
--cache-dir DIR         каталог кеша (cache)
--update                скачать свежие данные OSM
--rebuild               пересобрать транспортный PBF из кеша
--walk-minutes N        начальное время пешком (5 мин)
--walk-speed N          скорость ходьбы (80 м/мин)
--grid-size N           размер ячейки пространственного индекса (400 м)
--circle-opacity N      прозрачность зон доступности (40%)
```

Пример:

```bash
python3 build-transport-map.py \
    --walk-minutes 10 \
    --walk-speed 75 \
    -o routes.html
```

## Данные

Источник транспортных данных - OpenStreetMap / Geofabrik.

Транспортные данные упаковываются непосредственно в сгенерированный HTML. Leaflet,
MapLibre GL и векторные тайлы OpenFreeMap загружаются из сети, поэтому для отображения фона
карты необходим доступ в интернет. Векторная карта работает через адаптер MapLibre GL Leaflet;
поддержка WebGL необходима в браузере.

Подложка всегда использует OpenFreeMap Bright. Для новой обработки станций необходимо
пересоздать транспортный кеш: генератор автоматически создаст версию `v4`.

OpenFreeMap: https://openfreemap.org/ и https://openfreemap.org/quick_start/
OSM Tile Usage Policy: https://operations.osmfoundation.org/policies/tiles/

## MCC line rendering (v0.2.5)

An enabled MCC mode now shows the full *available* MCC network as a thin brown
reference line. It is intentionally independent of the reachability-colored
segments, so an inaccessible section is not mistaken for a reachable one.
The legend identifies the two kinds of line. If OSM route ways are incomplete
or cannot be matched to most stations, the line is approximated by connecting
ordered station coordinates. This approximation does not claim to follow tracks.

The in-browser console logs the number of MCC route directions, stops and
rendered line geometries. If no route geometry is available, inspect the
building diagnostics below: this drawing fix cannot create missing OSM data.

This is an HTML/JavaScript rendering change. It reuses the existing
`moscow-transport-v4.osm.pbf` cache without re-extraction.

## MCC data diagnostics (v0.2.4)

When MCC does not appear, check the console after building:

```text
MCC station fallback: found N/31 stations in OSM; existing route stops: M
  mcc: K route directions
```

If the full route relation is missing, the fallback uses local OSM station coordinates
and connects them in station order (straight segments, not actual tracks). If fewer than
24 stations are found, the fallback is not constructed and a warning is printed.
This does not modify the source PBF. The versioned v4 transport cache is rebuilt
on the first run; later runs reuse it. Enable the MCC toggle in the map to show it.
