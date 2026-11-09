# Scientific MLOps Core

Проект ядра версионирования данных и вычислительных экспериментов. Пока здесь находится архитектурный черновик с предлагаемыми решениями, а не описание реализованных гарантий.

Ядро определяет, что считается версией датасета и снимком условий эксперимента, как они связаны с конкретным запуском, как сравнить состояния и подготовить восстановление. Оно не управляет пользователями, веб-интерфейсом или вычислительным кластером.

Планируется использовать ядро как Go-модуль в [Scientific MLOps Platform](https://github.com/m1ssha/scientific-mlops-platform). Разделение репозиториев не требует разделения на два сетевых сервиса. Локальный CLI и файловые адаптеры позволят проверять ядро отдельно от платформы.

MVP ограничен одним доверенным пайплайном. Ядро проверяет обязательные материалы и строит план восстановления, CLI или внешний скрипт восстанавливает материалы и выполняет вычисление. Повторное выполнение получает отдельную запись со ссылкой на исходный снимок. Структурная проверка, исполнение и оценка метрик разделяются, правила численного сравнения задаются до основных испытаний. Актуальные уточнения находятся в ADR, архитектурные схемы показывают также возможные расширения.

- [Предлагаемые архитектурные решения (ADR)](docs/adr/README.md)
- [Архитектура и жизненный цикл версии](docs/architecture.md)
- [Компонентная карта и контракты](docs/components.md)
- [SVG-карта](docs/component-map.svg)

## Development baseline

- [MVP specification](docs/mvp-spec.md): requested behavior, invariants and open questions.
- [Local prototype contracts](docs/local-contracts.md): independently testable first slice.
- [Development instructions](AGENTS.md): roles, exact-commit review and integration.

These documents do not accept the proposed ADRs or imply that planned operations are implemented.

## Object prototype

The local object slice provides the Go `object.Store` interface, the
`storage/local` adapter and `smlops object put`, `verify`, and `get`. See the
[local contracts](docs/local-contracts.md#first-slice-verified-objects) for
arguments, JSON output and errors. Other operations described above remain
planned. Use Go 1.27.3; run `go test ./...` to check the implementation.

Objects are verified before publication and before an open returns. Atomic
create-if-absent prevents replacing an existing address. Cancellation after
publication can leave a valid object; verify its reference to determine its
state. The local Linux prototype assumes a trusted owner and filesystem and
makes no power-loss or hostile-owner guarantee.
