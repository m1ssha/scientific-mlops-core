# Контракты метаданных и восстановления, версия 1

Проект от 2026-11-09 для Issue #2. Документ описывает целевой контракт,
часть которого реализована в прототипе каталога datasets, snapshots и runs. Доступные команды
и ограничения перечислены в [README](../README.md#каталог-версий-данных);
сравнение и построение плана восстановления реализованы.
ADR 0003-0009 остаются proposed. Принятие существенных решений требует
независимого ревью и явного согласия сопровождающего проект.

## Профиль и решения на согласование

| Область | Предложение | Причина и цена |
| --- | --- | --- |
| Идентичность | Отдельные непрозрачные ID записей/операций, SHA-256 для байтов | Не объединяет разные выполнения; требует хранения результатов повторов |
| Локальный каталог | Один JSON-файл поколения, стабильный flock, атомарный rename | Проверяемый локальный прототип; переписывается весь каталог, нет общей транзакции с объектами |
| Инициализация | Явная операция, отдельный каталог metadata как постоянный признак начала инициализации | Потерянный каталог не становится пустой историей; незавершённый старт требует ручного восстановления |
| Восстановление | Проверка объявленного комплекта без сетевого резолвера и исполнения | Непроверенные внешние зависимости блокируют готовность; нет доказательства полноты объявленных условий |

Профиль локального прототипа: один узел Linux, локальная ФС с
flock, атомарным rename в одном каталоге, hard link и fsync файлов/каталогов,
доверенный владелец. Поддержку проверить в #4; сетевые ФС, удаление через API,
несколько узлов, защита от владельца и долговечность после отказа питания
не обещаются. Проверка целостности выявляет случайную порчу, не удостоверяет автора.

## Общие правила формата

Все приведённые ниже JSON-объекты закрытые: неизвестные и повторяющиеся ключи,
невалидный UTF-8, второй документ после первого и неверные типы отклоняются.
Поле schema_version обязательно и равно целому 1 на уровне envelope, Record,
Command, Receipt, Diff, RecoveryPlan и ответов CLI. Вложенные Body/TypedValue
не имеют отдельного поля версии. Неподдерживаемая версия
даёт unsupported_schema; не предпринимать миграцию или перезапись истории.
Числа NaN/Infinity запрещены. Целые поля принимают десятичную целочисленную
запись без дроби/экспоненты, помещающуюся в int64; счётчики неотрицательны.
JSON null допустим только в явно nullable полях. Обязательные массивы/карты
передаются даже пустыми. Необязательных скрытых значений по умолчанию нет.

ID проекта, записи и операции: ровно 32 строчные шестнадцатеричные цифры.
Проектный ID задаёт клиент при инициализации. Новые record ID генерирует ядро
из 16 байтов crypto/rand под транзакцией с проверкой уникальности среди всех
записей проекта; сбой генератора даёт storage, повтор ID - повтор генерации.
Operation ID задаёт клиент; его область - один проект, отдельно от record ID.
ID не является разрешением доступа. Перенос копии хранилища сохраняет project ID.

Ссылка на объект имеет ровно algorithm, digest, size по [контракту объектов](local-contracts.md).
Путь - непустой относительный UTF-8 путь с разделителем slash. Запрещены
начальный slash, backslash, NUL, пустые компоненты, компоненты dot/dot-dot,
дубликаты и коллизии «файл - родитель другого файла». Регистр и Unicode
не нормализуются. Целевые пути сравниваются после присоединения префиксов
входов; материализатор дополнительно предотвращает переход по симлинкам.
Переносимость имён на другие ОС не входит в профиль.

TypedValue - один из закрытых объектов:

```json
[{"type":"integer","value":7},{"type":"number","value":0.25},
 {"type":"string","value":"train"},{"type":"boolean","value":true},
 {"type":"null"}]
```

integer - int64; number - конечное binary64. Сравнение сохраняет тип:
integer 1 отличается от number 1.0; отсутствие параметра отличается от null
и пустой строки. Числа number сравниваются после декодирования в binary64,
минус ноль нормализуется в ноль. В MVP параметры скалярные; вложенные структуры
сохраняются отдельным материалом конфигурации, а не неявно интерпретируются.
Строковые ключи ролей/параметров/метрик/условий - непустые UTF-8 строки без NUL.
Времена - RFC3339Nano в UTC с Z либо null, когда не наблюдались.

## Записи и команды

Каждая запись содержит schema_version, id, kind, sequence и body. kind:
dataset, snapshot или run. sequence - уникальный положительный номер поколения
её публикации. На одну мутирующую команду создаётся не более одной записи.
Все ссылки ведут в тот же project ID и на записи предыдущих поколений;
это исключает циклы без реализации произвольного графа. Запись не меняется.

| Body | Обязательные поля и смысл |
| --- | --- |
| Dataset | predecessor_id: ID или null; files: массив {path, object}, допустим пустой. Предшественник - существующий dataset. Пути уникальны, отсортированы |
| Snapshot | predecessor_id: ID или null; profile_id: непустой ID версии внешнего паспорта; inputs: массив {role, dataset_id, target_prefix}; code: {repository, commit, entry_point}; parameters: карта TypedValue; split: {description, materials}; environment: {description, materials, conditions}; materials: массив Material |
| Run | snapshot_id; source_run_id: ID или null; started_at/finished_at: время или null; outcome: succeeded/failed/interrupted; reason: строка или null; observations: карта Observation; metrics: карта Metric; outputs: массив {path, role, media_type, object}; evidence_source: всегда caller_reported |

Material имеет name, purpose (code/split/environment/dependency/configuration),
required (boolean), path и source. source - ровно один вариант:

```json
[{"kind":"object","object":{"algorithm":"sha256","digest":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","size":0}},
 {"kind":"external","locator":"https://example.invalid/package.whl","expected_object":null}]
```

Для external expected_object - ссылка на ожидаемые байты или null. Locator
не используется для автоматического сетевого доступа; схема 1 не содержит
секретов авторизации. Вложенные ссылки внутри файлов не обнаруживаются автоматически.
Все datasets, code/material paths и outputs проверяются на коллизии отдельно
для входного рабочего каталога и каталога результатов.

Snapshot требует хотя бы один input с уникальной role, уникальные material name
и целевые пути. target_prefix - валидный непустой относительный путь.
code.repository - непустая строка, commit - полный Git hash (40 или 64 строчные
hex-цифры), entry_point - путь к обязательному code-материалу, без аргументов
и shell-команды. split.materials и environment.materials - непустые массивы
имён соответствующих обязательных материалов. description - непустая строка.
Условия environment.conditions - карта имени в ожидаемый TypedValue; она может
быть пустой, но это не доказывает отсутствие существенных условий.
Предшественник - snapshot. profile_id именует версию методики, не доказывая её
полноту; набор конкретных обязательных материалов и условий утверждается в #6.

Observation: {status, value, evidence}; status - passed/failed/unchecked;
value - TypedValue или null; evidence - непустая строка или null.
Ключ должен существовать в environment.conditions снимка. passed требует
value, равный ожидаемому по типу/значению, и evidence; failed требует evidence.
unchecked требует value=null. Пропущенное обязательное наблюдение считается
unchecked. Это утверждения клиента, а не проверка среды самим ядром.
Для succeeded нельзя записать известное failed-условие как прежние условия:
нужен новый снимок фактических условий. failed/interrupted могут сохранять
отклонения от условий задуманной попытки с явным reason. План такого запуска
восстанавливает заявленный снимок, не обещая повтор фактического отклонения.

Metric: {definition, unit, value}; definition и unit - непустые строки,
value - конечное binary64. Отсутствующая метрика не превращается в ноль.
Смысл definition/unit задаёт версия паспорта. Не интерпретировать метрику
только по отображаемому имени. Для численного сравнения требуются одинаковые
profile_id связанных снимков и одинаковые definition/unit. Разные профили
дают incomparable даже при совпавших строках определения, единицах и значениях:
схема 1 не предполагает глобального реестра эквивалентных определений.
При совместимости разность - right minus left; её переполнение даёт
incomparable, не Infinity.
Outputs уникальны по path, role/media_type непустые. Все перечисленные локальные
объекты outputs должны быть проверены при регистрации, неизвестные выходы
не добавляются. При failed/interrupted разрешены пустые outputs/metrics.
Для succeeded reason=null, иначе непустой reason обязателен. Когда известны оба
времени, finished_at не раньше started_at. Не придумывать конец прерванной
попытки. source_run_id, если задан, указывает на существующий run того же snapshot.

Мутирующая Command содержит schema_version, repository_id, operation_id, type,
body и ref_update. Для publish_dataset/save_snapshot/register_run body -
соответствующий Body без серверных id/sequence. ref_update - null либо
{name, expected_revision}; именная ссылка тогда указывает на новую запись
dataset/snapshot. Для run ref_update всегда null. name - непустая UTF-8 строка
без NUL, непереносимая в файловый путь. expected_revision=0 ожидает отсутствие;
положительное значение - точную существующую ревизию. Ревизия - int64, начинается
с 1, увеличивается при каждом изменении, переполнение отклоняется как conflict.
Удаление/переименование ссылок отсутствует, поэтому ревизия не сбрасывается.

Команда set_reference имеет body {name, target_kind, target_id, expected_revision}
и ref_update=null, записи не создаёт. target_kind только dataset/snapshot;
ссылка при обновлении сохраняет kind. Она позволяет явно вернуть указатель
на старую запись; ревизия всё равно растёт. Предшественник записи независим от
текущего указателя: ядро не выводит его из имени и не выполняет слияние.

Receipt: {schema_version, operation_id, record_kind, record_id, sequence,
reference}. Для set_reference record_kind/record_id=null. reference - null
либо {name, target_kind, target_id, revision}. sequence - поколение фиксации,
не текущее поколение при последующем retry. Отдельного флага replayed нет:
повтор возвращает тот же Receipt.

## Нормализация, повторы и атомарность

Нормализовать типизированный запрос после проверки формата: сортировать ключи
карт, files/outputs по path, inputs по role, materials по name, массивы имён
материалов по имени. Порядок этих коллекций не несёт значения. Дубликаты
отклонять, не удалять молча. Строки не обрезать, пути не чистить с потерей смысла.
Числа TypedValue нормализовать по правилам выше. Сравнивать полную нормализованную
команду, включая project ID, тип, все условия, reference expectation и operation ID;
серверный случайный record ID и время регистрации в неё не входят.
Нормализация относится к equality запроса, не к идентичности манифеста по хешу.

1. Валидировать синтаксис/типы и нормализовать команду, не загружая объекты.
2. Открыть существующий стабильный metadata/lock отдельным файловым дескриптором
   на транзакцию и получить исключительный Linux flock. Никогда не заменять inode
   lock. Ожидание блокировки должно прерываться context, без занятого цикла.
3. Прочитать и полностью проверить зафиксированный каталог. Сверить repository_id.
4. Искать operation_id раньше CAS и проверки текущей доступности объектов.
   Равная сохранённая команда возвращает прежний Receipt без изменения каталога.
   Другая команда даёт operation_conflict. Retry не перепроверяет сохранённые
   материалы и не подтверждает их текущую доступность.
5. Для новой операции проверить ссылки, CAS, бизнес-инварианты и полные байты
   всех прямо обязательных локальных объектов команды. Для snapshot проверить
   также объекты входных datasets и локальные материалы; внешние зависимости
   допустимы как явно unchecked, поэтому публикация snapshot не означает ready.
6. Создать запись/Receipt и следующее поколение в памяти; сохранить полную
   нормализованную команду и Receipt без ограничения срока в MVP. Ошибки до
   фиксации, в том числе конфликт, не сохранять как успешный результат операции.
7. Записать временный файл в metadata, fsync и close, проверить context, выполнить
   atomic rename поверх metadata/catalog.json. Это точка видимости всех изменений.
   Затем fsync каталога metadata, закрыть дескриптор и снять блокировку.

До rename отказ/отмена оставляет прежнее поколение; staging может остаться.
После rename ошибка синхронизации/закрытия или потерянный ответ дают uncertain,
не обещание отсутствия записи. Повтор с тем же operation ID разрешает исход.
Отмена после точки видимости может завершиться сохранённым Receipt; нельзя
сообщать определённую отмену, подразумевая отсутствие фиксации. Блокировка
освобождается ядром ОС после гибели процесса. Автоповтор с новой ревизией запрещён.
Объекты пишутся заранее вне этой блокировки; отсутствие API удаления предотвращает
гонку с поддерживаемыми операциями. Внешняя порча остаётся обнаруживаемой угрозой.

## Каталог, инициализация и чтение

Структура корня дополняет реализованные objects и staging:

```text
metadata/lock
metadata/catalog.json
metadata/.catalog-*   (никогда не источник чтения)
```

Явный InitRepository принимает project ID, требует существующий безопасный корень, создаёт
metadata через mkdir без замены; существующий metadata всегда даёт conflict.
Создаёт постоянный lock, fsync lock, пишет поколение 0 во временный файл,
fsync/close, rename, fsync metadata и корня (его родителя). Сам корень в этой операции не создаётся. Незавершённый init оставляет
metadata и блокирует повторный init; автоматического удаления/переинициализации
нет. Уже существующие объекты разрешены. Инициализация не имеет operation ID:
повтор после uncertain читает project ID/каталог, не вызывает init заново.

OpenRepository не создаёт метаданные. При отсутствии metadata - not_initialized;
при существующем metadata и отсутствии catalog.json/lock - integrity с деталями
incomplete_or_missing_catalog. Нельзя различить потерю истории и неполный init
по одному остатку; требуется ручная диагностика. Удаление всего metadata
неотличимо от хранилища только объектов: Open возвращает not_initialized,
но никогда не вызывает Init автоматически. Инициализация - явная операция
создания проекта, не способ восстановления утраченной истории. Порча lock/layout - integrity
или storage для системной I/O ошибки. Симлинки и нерегулярные служебные файлы
отклоняются. Не восстанавливать каталог из orphan-объектов или времени staging.

Envelope имеет ровно schema_version=1, payload_sha256 (64 строчные hex-цифры)
и payload (JSON-объект). Хешировать точные UTF-8 байты значения payload от
открывающей до соответствующей закрывающей фигурной скобки, включая внутренние
пробелы, исключая пробелы внешнего envelope. Сначала проверить checksum исходного
среза, затем разбирать payload. Пересериализация payload до проверки запрещена.
Читатель открывает catalog.json один раз и читает один inode целиком, без повторного
открытия его частей. Читатели не блокируют писателя; видят старое или новое полное
поколение. Файлы разных поколений не смешиваются в одном ответе/плане/diff.

Payload содержит ровно repository_id, sequence, records, references, operations.
records - карта ID -> Record, ключ совпадает с id; references - карта name ->
{target_kind,target_id,revision}; operations - карта operation ID -> {command,result}.
Начальное sequence=0, карты пусты. Новая успешная операция увеличивает sequence
на 1; retry не увеличивает. Номера записей/Receipt не превышают sequence,
разные успешные операции имеют разные sequence. Карты и ссылки, соответствие
Receipt/команды/записи, виды и существование целей проверяются при чтении.
Ошибочная семантика каталога даёт integrity, а не пустую историю. Ни один
прежний record/operation result нельзя менять при добавлении поколения.

Пагинация списка: sequence по возрастанию, после after_sequence (исключительно),
limit - положительный int64; ответ имеет view_sequence и next_after_sequence
(null в конце). Для воспроизводимой страницы клиент закрепляет view_sequence;
если текущий каталог уже новее, читаются только записи с sequence не выше
закреплённого значения. Новые записи не меняют прежнюю страницу. История -
точные predecessor/source-связи, не сортировка по неизвестному времени.

## API, CLI и ошибки

Пакет catalog реализует срез dataset, snapshot, run и ссылок; актуальную поверхность см. в
[README](../README.md#каталог-версий-данных) и Go-коде. Следующая целевая
поверхность включает Diff и RecoveryPlan. Имя Go поля - PascalCase
соответствующего snake_case, ID/JSON сохраняют общепринятые аббревиатуры.
Поверхность Service:

```go
PublishDataset(context.Context, Command) (Receipt, error)
SaveSnapshot(context.Context, Command) (Receipt, error)
RegisterRun(context.Context, Command) (Receipt, error)
SetReference(context.Context, Command) (Receipt, error)
GetOperation(context.Context, ID) (Receipt, error)
Repository(context.Context) (View, error)
Compare(context.Context, RecordKind, ID, ID) (Diff, error)
PlanRecovery(context.Context, ID) (RecoveryPlan, error)
```

Каждый мутирующий метод требует соответствующий type. RecordKind - строковый
тип dataset/snapshot/run. View - независимое представление проверенного
поколения с GetRecord, GetReference и ListRecords; изменение клиентской копии
не меняет хранилище. Compare и PlanRecovery принадлежат Service: каждый читает
один View, PlanRecovery дополнительно использует отдельный object.Store.
Compare принимает kind и два ID одного вида; PlanRecovery принимает run ID.
Порт метаданных: Read(ctx) (View,error) и Update(ctx, func(Tx) error) error.
Tx предоставляет текущий View и методы добавления Record/operation result/CAS;
изменения фиксируются вместе только при nil callback. Callback вызывается
однократно под блокировкой, без автоматического повторения/вложенной транзакции.
Бизнес-правила остаются в Service, атомарность/блокировка - в адаптере.
Object Store остаётся отдельным портом, его интерфейс не меняется.
Точные экспортируемые Go-типы Tx/View закрепляются в соответствующем кодовом
срезе без изменения этих наблюдаемых правил.

```text
smlops repository init --store DIR --repository-id ID
smlops repository show --store DIR
smlops ref show --store DIR --name NAME
smlops dataset publish --store DIR --request FILE
smlops snapshot create --store DIR --request FILE
smlops run register --store DIR --request FILE
smlops ref set --store DIR --request FILE
smlops operation show --store DIR --id ID
smlops dataset|snapshot|run show --store DIR --id ID
smlops dataset|snapshot|run list --store DIR --after-sequence N --limit N --view-sequence N
smlops diff dataset|snapshot|run --store DIR --left ID --right ID
smlops recovery plan --store DIR --run ID
```

В этом списке реализованы repository init/show, dataset publish/show/list,
snapshot create/show/list, run register/show/list, ref set/show, operation show,
diff и recovery plan.
FILE - Command JSON без угадываемых ID/CAS по текущему
имени. Объекты загружаются отдельной object put. init возвращает
{schema_version,repository_id,sequence:0}; остальные success - Receipt либо
{schema_version,view_sequence,record}, {schema_version,view_sequence,items,
next_after_sequence}, {schema_version,view_sequence,reference}, Diff/RecoveryPlan.
operation show возвращает прежний Receipt. Список всегда требует все три
параметра пагинации: after=0 и view из Read для первой страницы. Для CLI получение
view - repository show; оно возвращает schema_version,repository_id,sequence.

Ошибки CLI имеют envelope {error:{code,message}}. Поле details с
контекстом ошибки предусмотрено целевым контрактом, но пока не реализовано.
Успех - одна JSON-запись stdout, ошибка - одна stderr, без ложного success.
invalid_input даёт exit 2, остальные ошибки - exit 1. Стабильные коды:
not_initialized, not_found, missing_dependency, integrity, conflict,
operation_conflict, unsupported_schema, condition_mismatch, storage, uncertain,
cancelled, deadline; реализованные object-коды сохраняются. Текущий dataset-срез
также сообщает unsupported_kind для сохранённых видов записей или команд,
которые он не поддерживает; он не перезаписывает такой каталог.
Сервис экспортирует соответствующие Err... для errors.Is; ошибки object-порта
не теряют исходную категорию. Отсутствующий объект при новой публикации даёт
missing_dependency; существующий испорченный - integrity; I/O не становится missing.
В recovery проблемы материалов входят в отчёт, ошибка чтения каталога - ошибка
операции без частичного равенства/готовности.

Diff имеет ровно schema_version, view_sequence, kind, left_id, right_id,
changes. Это одна плоская форма ответа для всех видов, без вложенных секций
changes. Сравнивается нормализованная проекция, а не позиции массивов исходного
Record. Поля id, sequence и predecessor_id не участвуют в равенстве условий.

| kind | Проекция для сравнения |
| --- | --- |
| dataset | files: карта path -> object |
| snapshot | profile_id; inputs: карта role -> {dataset_id,target_prefix}; code; parameters; split; environment; materials: карта name -> Material |
| run | conditions: проекция связанного snapshot; provenance: {snapshot_id,source_run_id}; timing: {started_at,finished_at}; outcome; reason; evidence_source; observations; metrics; outputs: карта path -> {role,media_type,object} |

Change имеет ровно path, kind, left, right, delta. kind:
added/removed/changed/incomparable. path - JSON Pointer в эту проекцию:
slash/tilde в ключе экранируются как ~1/~0. Упорядоченные массивы имён материалов
сравниваются целиком; карты обходятся по ключам. Элементы files, inputs,
materials, parameters, observations, metrics и outputs сравниваются атомарно
по целому значению элемента; остальные объекты - по полям, scalar - по значению.
Сортировка Changes - по path, затем kind, без дублирования изменений родителя
и его потомка. Массив changes пуст только при отсутствии различий и препятствий.

left/right содержат сравниваемые сохранённые значения; для отсутствующей стороны
используется null, а kind показывает отсутствие: added означает, что слева нет
ключа, removed - что справа нет ключа. changed с left=null означает явно
сохранённый null, а не отсутствие. delta - конечная числовая разность совместимых
метрик; во всех других случаях null. Разные profile_id/definition/unit дают
incomparable даже при одинаковом значении; отсутствие метрики даёт added/removed,
а не разность от нуля. Для совместимых неизменившихся метрик Change не создаётся.

У двух run одного snapshot нет различий в conditions; разные run ID остаются
в left_id/right_id. Происхождение повтора, времена, наблюдения и evidence_source
участвуют в собственных полях проекции run, не в равенстве условий. Текущая
доступность файлов, допуски и ранжирование в Diff не входят.
[Примеры diff](contracts/v1/diff-examples.json) относятся к тому же каталогу:
переименование показано removed+added; явный null исходного source_run_id - changed.

## План восстановления

RecoveryPlan имеет schema_version, repository_id, view_sequence, source_run_id,
snapshot_id, checked_at, coverage=declared_only, conditions (полный Snapshot Body),
materials, blockers, actions, stored_materials_ready, runtime_ready, execution,
numerical_agreement. Последние три всегда unchecked у ядра: план ничего не исполняет.
runtime_ready - unchecked до внешней проверки; stored_materials_ready - boolean.

PlanMaterial: {key,path,purpose,required,source,check,checked_at,detail}.
Dataset-файлы разворачиваются в отдельные элементы с purpose=input;
snapshot материалы сохраняют purpose/required, исходные outputs добавляются
с purpose=original_output и required=false. key - стабильный уникальный
идентификатор элемента в плане: компоненты роли, имени и пути кодируются отдельно в формате URL query escaping, а типы разделяются двоеточием. Check: verified/missing/corrupt/unchecked/io_error.
verified требует полного чтения размера/хеша; checked_at обязателен кроме
unchecked (там null). Для external всегда unchecked, даже при expected_object:
наличие локальных байтов по похожей ссылке не заменяет внешний источник молча.
io_error содержит причину чтения, не превращается в missing.

stored_materials_ready=true только если все required элементы verified.
Пустой/неполный набор не получает полноты автоматически: coverage всегда
declared_only, фактический обязательный комплект задаёт паспорт #6.
Blocker: {material_key,code,message}, по одному для каждого непройденного
required-элемента. Отсутствующий необязательный original_output не блокирует
подготовку входов, но мешает сравнению соответствующих выходных байтов.

Action: {id,type,material_key,depends_on}. Типы obtain, verify, materialize,
prepare_environment, validate_conditions, execute_trusted, register_run.
material_key=null для глобальных действий. Идентификаторы уникальны, depends_on
ссылаются только назад в массиве; это план зависимостей, не универсальный executor.
Для каждого входа obtain -> verify -> materialize, prepare_environment зависит
от всех обязательных materialize, validate_conditions - от prepare_environment,
execute_trusted - от validate_conditions, register_run - от execute_trusted.
Последние два - только подсказки внешнему доверенному скрипту, не команды shell.
Blockers запрещают продвижение соответствующей ветви. Перед использованием
материализатор повторно проверяет байты и безопасные пути; план не резервирует данные.
Структурный успех требует и verified материалов, и внешней проверки обязательных
условий; успешное выполнение и численное согласие оцениваются отдельно.

## Примеры и границы приёмки

[Пример каталога и команд](contracts/v1/catalog-example.json) содержит две
разные попытки одного снимка и новую версию данных. [Заблокированный план](contracts/v1/recovery-example.json)
показывает все пять исходов проверки и внешнюю непроверенную зависимость.
Это синтетические контрактные примеры, не результаты ML-испытаний.

Перед #4: проверить init/потерю каталога, checksum/схему, два процесса CAS,
ABA, равный retry и изменённый запрос, неизвестный исход после rename,
гибель процесса/lock, читателя во время commit, сохранность прежних записей.
Перед #5: два run одного snapshot, retry регистрации, failed/interrupted без
выдуманных наблюдений, неверные ссылки и типизированные параметры.
Перед recovery: missing/corrupt/io_error/unchecked required блокируют ready,
необязательный output не блокирует входы, последующая потеря требует перепроверки.
При разборе Command нарушение схемы даёт invalid_input; то же нарушение
в сохранённом каталоге - integrity, кроме явно неподдерживаемой версии
(unsupported_schema). Сохранённый commit - утверждение клиента о происхождении
кода; соответствие сохранённых файлов Git-коммиту требует отдельной внешней
проверки. PlanMaterial с purpose=original_output материализуется в отдельный
каталог исходных результатов, остальные - в каталог входных условий.

Реализованный dataset-срез содержит тесты для ряда этих сценариев; см. тесты
пакета catalog. Проверки будущих возможностей остаются невыполненными.
