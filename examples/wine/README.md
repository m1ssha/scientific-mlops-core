# Учебный эксперимент Wine

Самостоятельный CPU-пример, не зависящий от ядра. Протокол `protocol.json` пока draft: пилоты не являются основными испытаниями. Полные условия приведены в `docs/wine-experiment-design.md` репозитория. Фиксированные входы включают данные UCI, разбиение с 1-based row ID, параметры, код, протокол и lock; manifest.json содержит SHA-256 каждого файла, кроме себя. Изменения требуют нового манифеста и делают условия другими. Манифест удостоверяет целостность относительно самого себя, а не подлинность источника.

Нужны CPython 3.12.15, Linux x86_64 и зависимости из requirements.lock. Python и wheels не включены; их доступность для восстановления отдельно от текущей среды. Например, в новой среде:

```sh
python -m pip install --require-hashes -r examples/wine/requirements.lock
python examples/wine/pipeline.py verify --root examples/wine
python examples/wine/pipeline.py verify --root examples/wine --wheelhouse /path/to/wheels
python -m unittest discover -s examples/wine -p 'test_*.py'
```

Скопируйте весь каталог examples/wine в каждый чистый каталог и выполняйте именно его код. Выходной каталог должен отсутствовать, а его родитель — существовать. Команды повторно обучают модель, не используют выходы эталона:

```sh
python bundle-a/pipeline.py run --phase validation --root bundle-a --out result-a
python bundle-b/pipeline.py run --phase validation --root bundle-b --out result-b
python bundle-a/pipeline.py compare --root-a bundle-a --a result-a --root-b bundle-b --b result-b
```

Команды печатают JSON и завершаются с ненулевым кодом при ошибке или отсутствии численного pass. `result.json` содержит уникальный ID, времена и хеши; при ошибке выполнения создаётся запись failed. Данные и предсказания не являются доказательством переносимости. Сравнение проверяет предсказания и метрики независимо от обучения, включая метки из исходного файла и порядок разбиения. Для создания манифеста после намеренного изменения материалов служит `python bundle/pipeline.py manifest --root bundle`; проверяйте изменения отдельно. Генерация исходного разбиения в закреплённой среде: `python examples/wine/prepare_split.py > split.generated.json`; сравните байты с examples/wine/split.json. Команда генерации не заменяет существующее разбиение.

Фазы: `pilot` — пилот, `validation` — проверка инструмента (по умолчанию), `main` — основное испытание. Последняя разрешена только при статусе frozen и проверенной записи freeze с SHA-256 pipeline.py и prepare_split.py, 40-символьным Git SHA версии кода и временем фиксации UTC; канонический параметр C=1.0. Контрольное значение C=2.0 заранее объявлено для отдельного запуска и не считается основным испытанием. Перед записью результата код проверяет соответствие активного файла каталогу входов и неизменность всех материалов с начала запуска. Сравнение возвращает отдельные статусы: materials equal/different/invalid, conditions equal/different/invalid/unchecked, execution valid/failed/invalid/unchecked, numerical pass/different/incomparable/unchecked.
 Для фиксации протокола после ревью явно обновляют `status` на `frozen`, поле `freeze` на объект `{"code_revision": "<40 hex SHA>", "code_files": {"pipeline.py": "<SHA-256>", "prepare_split.py": "<SHA-256>"}, "frozen_at": "<ISO-8601 UTC>"}` и затем заново создают манифест. Протокол и манифест в code_files не входят. Самостоятельной фиксации после пилотов не происходит.
 Поле code_revision сохраняет заявленную версию кода; автономная проверка файлов не доказывает происхождение Git-коммита.
