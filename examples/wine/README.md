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
python bundle-a/pipeline.py run --root bundle-a --out result-a
python bundle-b/pipeline.py run --root bundle-b --out result-b
python bundle-a/pipeline.py compare --root-a bundle-a --a result-a --root-b bundle-b --b result-b
```

Команды печатают JSON и завершаются с ненулевым кодом при ошибке или отсутствии численного pass. `result.json` содержит уникальный ID, времена и хеши; при ошибке выполнения создаётся запись failed. Данные и предсказания не являются доказательством переносимости. Сравнение проверяет предсказания и метрики независимо от обучения, включая метки из исходного файла и порядок разбиения. Для создания манифеста после намеренного изменения материалов служит `python bundle/pipeline.py manifest --root bundle`; проверяйте изменения отдельно. Генерация исходного разбиения в закреплённой среде: `python examples/wine/prepare_split.py > split.generated.json`; сравните байты с examples/wine/split.json. Команда генерации не заменяет существующее разбиение.
