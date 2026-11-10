# Scientific MLOps Core

Ядро для версионирования данных и условий вычислительных экспериментов. Цель проекта - сохранить связь между данными, кодом, параметрами, окружением и результатами, чтобы сравнивать запуски и готовить повторное выполнение эксперимента.

> [!IMPORTANT]
> Проект находится в разработке. Сейчас доступны локальное хранилище объектов, прототип каталога версий данных, снимков и запусков и самостоятельный учебный ML-пример. Сравнение и восстановление ещё не реализованы.

## Что работает

- Go API для записи, проверки и чтения объектов по SHA-256 и размеру.
- Локальный файловый адаптер и CLI с командами object put, object verify и object get.
- Явная инициализация репозитория, публикация и чтение версий данных, чтение и условное обновление именных ссылок, просмотр результата операции.
- Сохранение снимков условий и регистрация отдельных запусков через Go API и CLI.
- [Wine-пайплайн](examples/wine/README.md) для классификации с зафиксированным протоколом и результатами локальных повторов. Он выполняется самостоятельно и пока не интегрирован с ядром.

При записи хранилище проверяет содержимое и публикует объект без замены существующего. Чтение предваряется проверкой размера и хеша; CLI не перезаписывает файл назначения. Контракт и формат ошибок описаны в [документации](docs/local-contracts.md#first-slice-verified-objects).

## Быстрый старт

Для сборки нужен Go 1.27.3. Пример рассчитан на Linux и запускается из корня клонированного репозитория; также нужны стандартные утилиты sha256sum и wc.

```bash
go build -o smlops ./cmd/smlops
printf 'hello scientific mlops\n' > example.txt
digest=$(sha256sum example.txt | cut -d ' ' -f 1)
size=$(wc -c < example.txt | tr -d ' ')

./smlops object put --store ./example-store --file example.txt --sha256 "$digest" --size "$size"
./smlops object verify --store ./example-store --sha256 "$digest" --size "$size"
./smlops object get --store ./example-store --sha256 "$digest" --size "$size" --output restored.txt
```

Каждая успешная команда возвращает JSON. Повторный get с тем же путём завершится ошибкой, поскольку файл уже существует. Команды создают бинарный файл, два примера файлов и каталог хранилища в текущем каталоге.

Запуск тестов:

```bash
go test ./...
```

## Каталог версий данных

Каталог поддерживает записи dataset, snapshot и run. В Go пакет `catalog` предоставляет
`NewLocalMetadata`, `NewService` и методы `InitRepository`, `Repository`,
`PublishDataset`, `GetDataset`, `ListDatasets`, `SetReference`, `GetReference`
и `GetOperation`. Файловый адаптер реализует порт `MetadataStore` с `Read` и
`Update`; объектное хранилище остаётся отдельным портом.

Продолжение примера выше: `example-store` уже существует после object put.
Инициализация требует существующий безопасный корневой каталог и выполняется
явно; она не создаёт корень. ID репозитория и операций задаёт клиент: это ровно
32 строчные шестнадцатеричные цифры. Для каждого нового намерения нужен новый
operation ID.

```bash
./smlops repository init --store ./example-store --repository-id 00000000000000000000000000000001
cat > dataset-request.json <<EOF
{
  "schema_version": 1,
  "repository_id": "00000000000000000000000000000001",
  "operation_id": "00000000000000000000000000000002",
  "type": "publish_dataset",
  "body": {
    "predecessor_id": null,
    "files": [{"path": "example.txt", "object": {"algorithm": "sha256", "digest": "$digest", "size": $size}}]
  },
  "ref_update": {"name": "main", "expected_revision": 0}
}
EOF
./smlops dataset publish --store ./example-store --request dataset-request.json
./smlops repository show --store ./example-store
./smlops dataset list --store ./example-store --after-sequence 0 --limit 10 --view-sequence 1
./smlops ref show --store ./example-store --name main
./smlops operation show --store ./example-store --id 00000000000000000000000000000002
```

Первичная публикация создаёт запись и ссылку `main`; `expected_revision: 0`
означает ожидание отсутствия ссылки. Файлы должны быть предварительно загружены
через object put; публикация проверяет их размер и хеш. Полученный `record_id`
можно передать в `dataset show --store DIR --id ID`. Для обновления существующей
ссылки используется `ref set --store DIR --request FILE`: JSON-команда имеет
тот же envelope, `type: "set_reference"`, `ref_update: null` и body с полями
`name`, `target_kind: "dataset"`, `target_id` и `expected_revision`. В качестве
ожидаемой ревизии передайте ревизию из ref show; конфликт не обновляет ссылку.

Для первой страницы списка возьмите `sequence` из repository show и укажите его
как `--view-sequence`, вместе с `--after-sequence 0` и положительным `--limit`.
Для следующей страницы передайте `next_after_sequence` из ответа как
`--after-sequence`, сохранив тот же `--view-sequence`. Значение null означает конец.

Повтор той же нормализованной команды с тем же operation ID возвращает сохранённый
результат без новой проверки объектов и изменения каталога; другие входы с тем же
ID дают `operation_conflict`. После ошибки `uncertain` проверьте operation show
или повторите ту же команду с тем же ID. При неопределённом результате init
проверьте repository show: у init нет operation ID. Если каталог metadata есть,
но lock или catalog отсутствует, потребуется ручная диагностика; повторный init
завершится конфликтом и не восстанавливает историю автоматически.

Каталог с неизвестным видом записи или команды отклоняется с `unsupported_kind`
и не перезаписывается. Сравнение и восстановление пока не реализованы; ADR
остаются proposed.

## Снимки и запуски

Команды `snapshot create` и `run register` принимают JSON envelope версии 1 через
`--store DIR --request FILE`; схемы и примеры приведены в [контрактах](docs/contracts-v1.md)
и [каталоге-примере](docs/contracts/v1/catalog-example.json). Записи доступны через
`snapshot show/list` и `run show/list` с теми же аргументами пагинации, что и dataset.
В Go доступны `SaveSnapshot`, `RegisterRun`, `GetSnapshot`, `GetRun`,
`ListSnapshots`, `ListRuns`. Снимок ссылается на опубликованные данные; каждый
осознанный запуск получает отдельный ID. Повтор с тем же operation ID и той же
командой возвращает прежний Receipt. Снимок проверяет входные объекты и локальные
материалы; внешний источник сохраняется без сетевого доступа и не подтверждает
готовность. Запуск хранит сведения, переданные клиентом, и проверяет выходные
объекты; ядро не выполняет эксперимент и не подтверждает наблюдения среды.

Go API записи изменился: поле `Record.Body` теперь содержит конкретный `DatasetBody`,
`SnapshotBody` либо `RunBody` согласно `Record.Kind`. Клиенты прежнего dataset API
должны привести `record.Body` к `catalog.DatasetBody` перед обращением к его полям.
Формат существующего JSON-каталога версии 1 не менялся.

## Границы прототипа

Хранилище рассчитано на локальную файловую систему Linux и доверенного владельца. Проверка хеша обнаруживает повреждение содержимого, но не подтверждает авторство, выполнение эксперимента или воспроизводимость результатов. Сохранность при потере питания не гарантируется. Отмена после публикации может оставить корректный объект; его состояние можно проверить отдельно.

В целевом минимальном сценарии ядро проверяет материалы и строит план восстановления, а CLI или внешний скрипт готовит среду и выполняет доверенный пайплайн. Повторный запуск должен получить отдельную запись со ссылкой на исходный снимок. Это проектируемое поведение, а не доступная сейчас функция.

## Документация

- [Спецификация MVP](docs/mvp-spec.md) и [контракты версии 1](docs/contracts-v1.md).
- [Архитектура](docs/architecture.md), [компоненты](docs/components.md) и [схема](docs/component-map.svg).
- [Предлагаемые архитектурные решения](docs/adr/README.md) - ADR остаются в статусе proposed.
- [Дизайн Wine-эксперимента](docs/wine-experiment-design.md) и [результаты с независимым ревью](examples/wine/evidence/REPORT.md). Локальные повторы не подтверждают переносимость или обобщающую способность модели.

[Scientific MLOps Platform](https://github.com/m1ssha/scientific-mlops-platform) - отдельный проект пользовательского интерфейса и API поверх ядра; его реализация отложена.
