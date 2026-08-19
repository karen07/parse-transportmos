# Moscow Transport Reachability Map

Moscow Transport Reachability Map is an interactive browser map for exploring public-transport accessibility in Moscow using OpenStreetMap data.

`build-transport-map.py` downloads and preprocesses OSM data, extracts Moscow bus, tram, and trolleybus routes and stops, and generates `routes.html` with the transport data embedded in the page. Leaflet and the OpenStreetMap background tiles are loaded from the network.

The page can calculate approximate reachable areas with zero, one, or two transfers and can build route variants to a selected destination. It is designed for exploratory accessibility analysis rather than exact trip planning: walking distance is approximated geometrically and vehicle travel time is estimated from the transport graph rather than a live timetable.

## Описание

Moscow Transport Reachability Map - интерактивная браузерная карта для исследования транспортной доступности в Москве на основе данных OpenStreetMap.

`build-transport-map.py` загружает и предварительно обрабатывает данные OSM, извлекает московские маршруты и остановки автобусов, трамваев и троллейбусов и генерирует `routes.html` со встроенными транспортными данными. Библиотека Leaflet и фоновые тайлы OpenStreetMap загружаются из сети.

Страница может рассчитывать приблизительные зоны доступности без пересадок, с одной или двумя пересадками, а также строить варианты маршрута до выбранной точки. Проект предназначен для исследовательского анализа доступности, а не для точного планирования поездок: расстояние пешком оценивается геометрически, а время движения транспорта рассчитывается по транспортному графу, а не по актуальному расписанию.

## Готовая версия

Уже собранная версия карты доступна по адресу:

https://bus-routing.e7s.site/

Версия на сайте может немного отставать от текущей версии в репозитории.

## Возможности

- автобусы, трамваи и троллейбусы Московского транспорта;
- отображение маршрутов и остановок;
- расчет доступности от произвольной точки;
- 0, 1 или 2 пересадки;
- ограничение общего времени поездки;
- настройка допустимого времени пешком;
- построение маршрута до произвольной точки;
- несколько вариантов маршрута;
- адаптивный интерфейс для desktop/mobile.

Расстояние пешком рассчитывается по прямой, а время движения транспорта является оценочным, поэтому карта предназначена прежде всего для анализа транспортной доступности, а не для точного планирования поездок.

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

При первом запуске скрипт скачает PBF Центрального федерального округа с Geofabrik, создаст транспортный кеш и сгенерирует:

```text
routes.html
cache/
  central-fed-district.osm.pbf
  moscow-transport.osm.pbf
```

После этого достаточно открыть `routes.html` в браузере.

## Использование карты

Один клик по карте выбирает начальную точку и показывает доступную транспортную сеть.

В панели можно изменить:

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

Обычный запуск повторно использует существующий кеш.

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

Транспортные данные упаковываются непосредственно в сгенерированный HTML. Leaflet и фоновые тайлы OpenStreetMap загружаются из сети, поэтому для полноценного отображения карты требуется доступ в интернет.
