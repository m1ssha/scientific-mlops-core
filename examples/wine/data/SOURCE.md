# Происхождение Wine

Исходные байты wine.data получены 2026-11-09 из официального
[архива UCI](https://archive.ics.uci.edu/static/public/109/wine.zip).
[Карточка набора](https://archive.ics.uci.edu/dataset/109/wine), DOI:
[10.24432/C5PC7J](https://doi.org/10.24432/C5PC7J).
Авторы: Stefan Aeberhard и M. Forina; библиографическая запись UCI - 1992 год.
Лицензия источника: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Данные распространяются с этой атрибуцией; исходный файл не преобразован.

wine.data: 10 782 байта, SHA-256
6be6b1203f3d51df0b553a70e57b8a723cd405683958204f96d23d7cd6aea659.
SHA-256 полученного ZIP:
2bae62c4481220623579d4c4fb36b55652b6b75e06e49fa1981b8198362dfdab.

Файл без заголовка: первый столбец - класс 1/2/3, затем 13 признаков.
178 записей; классы содержат 59/71/48 наблюдений. row_id в примере - номер
строки исходного файла начиная с 1, а не измеряемый признак. Признаки:
alcohol, malic acid, ash, alcalinity of ash, magnesium, total phenols,
flavanoids, nonflavanoid phenols, proanthocyanins, color intensity, hue,
OD280/OD315 of diluted wines, proline. Единицы не добавляются от себя.

Это распознавание трёх сортов по химическим измерениям, не прогноз качества
вина и не рекомендация потребителю. Набор небольшой и сравнительно простой;
он используется как инженерный пример восстановления эксперимента.
