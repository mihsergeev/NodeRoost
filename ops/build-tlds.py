#!/usr/bin/env python3
"""Обновить список публичных доменов верхнего уровня из корневой зоны IANA.

    python ops/build-tlds.py

Этот список — то, что корень панели ОТКАЗЫВАЕТСЯ подписывать. Смысл ровно один:
корень стоит в доверенных на всех ваших машинах, и без запрета захваченная панель
выписала бы сертификат на банк или почту, а машины бы ему поверили. Запрещая всё,
что реально существует в интернете, мы оставляем свободными выдуманные домены
(`mesh`, `bironex`, `mirabah`) — а именно они и нужны внутри сети. Новый проект
не требует ничего менять: его домена в интернете нет, значит он и не запрещён.

Список меняется редко (ICANN делегирует новые TLD раунды́ми, не каждый день).
Обновлять его нужно, только если планируете перевыпускать корень: у уже выданного
корня ограничения зашиты внутрь и обновлением файла не изменятся.
"""

import datetime
import pathlib
import urllib.request

SRC = "https://data.iana.org/TLD/tlds-alpha-by-domain.txt"
DST = pathlib.Path(__file__).resolve().parent.parent / "backend" / "app" / "data" / "public_tlds.txt"


def main() -> None:
    raw = urllib.request.urlopen(SRC, timeout=60).read().decode()
    tlds = sorted(
        line.strip().lower()
        for line in raw.splitlines()
        if line.strip() and not line.startswith("#")
    )
    if len(tlds) < 500:  # список сломался — не затираем рабочий файл огрызком
        raise SystemExit(f"подозрительно мало TLD ({len(tlds)}) — источник изменился?")
    today = datetime.date.today().isoformat()
    body = "\n".join(tlds)
    DST.write_text(
        f"# Корневая зона IANA ({SRC}), снято {today}: {len(tlds)} доменов.\n"
        f"# Их корень панели подписывать НЕ будет — см. ops/build-tlds.py.\n{body}\n",
        encoding="utf-8",
    )
    print(f"{DST}: {len(tlds)} доменов")


if __name__ == "__main__":
    main()
