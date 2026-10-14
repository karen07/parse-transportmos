# Moscow Transport Reachability Map v0.4.0

Один исходный Python-файл, один генерируемый HTML-файл.

## Структура

```text
parse-transportmos/
  build-transport-map.py
  README.md
  cache/
  routes.html
```

Вся логика извлечения OSM, индексации, диагностики, CSS и JavaScript
находится в `build-transport-map.py`. Внешние HTML-шаблоны и файлы
JavaScript не используются. В сгенерированном HTML JavaScript открыт
для чтения и проверки. Только данные транспортного графа сжаты gzip.

## Требования

- Python 3.10 или новее.
- osmium для обработки исходного PBF: `python3 -m pip install osmium`.
- Интернет в браузере для Leaflet, MapLibre и картографической подложки.

## Запуск

```bash
cd parse-transportmos
./build-transport-map.py --report-json report.json
```

Кеш `cache/moscow-transport-v4.osm.pbf` совместим с этой версией.
Карта записывается в `routes.html`. По умолчанию включены автобус,
трамвай и троллейбус; метро, МЦК и МЦД включаются отдельно.
Подложка OpenFreeMap Bright фиксирована.

Дополнительные возможности:

```bash
./build-transport-map.py --version
./build-transport-map.py --update
./build-transport-map.py --rebuild
./build-transport-map.py --report-json report.json
./build-transport-map.py --input-geojson fixture.geojson -o routes.html
```

При отсутствии реальных маршрутных отношений OSM искусственный маршрут
МЦК из списка станций не создается. Геометрия, построенная по остановкам,
допускается для изображения существующего маршрута, но сама не создает
новых транспортных связей. Времена движения считаются отдельно от линий.

## Переход с v0.3.x

Нужно удалить старые каталоги `transportmap`, `tests` и `assets` и
отдельные модули, оставшиеся в корне после предыдущей распаковки.
Они больше не используются; кеш OSM при этом удалять не нужно.

Для обновления из корня репозитория распакуй архив, а затем удали
перечисленные старые компоненты проекта. Не удаляй `cache` и `routes.html`:
последний будет заменен при следующем запуске генератора.

## Форматирование

`format-go` может форматировать исходный Python и сгенерированный HTML.
Шаблонов на диске нет. Генератор проверяет ASCII и максимальную длину
строки 100 символов для готового HTML. Работу реального HTML Tidy на
этой версии нужно дополнительно подтвердить в окружении проекта.
