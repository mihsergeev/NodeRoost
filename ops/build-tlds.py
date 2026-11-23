#!/usr/bin/env python3
"""Обновить список публичных доменов верхнего уровня из корневой зоны IANA.

    python ops/build-tlds.py

Этот список — то, что корень панели ОТКАЗЫВАЕТСЯ подписывать. Смысл ровно один:
корень стоит в доверенных на всех ваших машинах, и без запрета захваченная панель
выписала бы сертификат на банк или почту, а машины бы ему поверили. Запрещая всё,
что реально существует в интернете, мы оставляем свободными выдуманные домены
(`mesh`, `acme`, `lab`) — а именно они и нужны внутри сети. Новый проект
не требует ничего менять: его домена в интернете нет, значит он и не запрещён.

Список меняется редко (ICANN делегирует новые TLD раунды́ми, не каждый день).
У уже выданного корня ограничения зашиты внутрь и обновлением файла не изменятся:
панель покажет, что корень пора выпустить заново, а подписывать новые публичные
домены откажется сразу.

Домены, которые IANA сняла, из списка не удаляются: запрет лишнего никому не
мешает, а agent_tlds.txt (замороженный список, по которому агент проверяет
корень) обязан оставаться частью этого - иначе новые корни не пройдут проверку
на нодах.
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
    # снятые IANA домены оставляем: см. выше про agent_tlds.txt
    if DST.exists():
        tlds = sorted(set(tlds) | {
            line.strip().lower()
            for line in DST.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        })
    today = datetime.date.today().isoformat()
    body = "\n".join(tlds)
    DST.write_text(
        f"# Корневая зона IANA ({SRC}), снято {today}, со снятыми ранее: {len(tlds)} доменов.\n"
        f"# Их корень панели подписывать НЕ будет — см. ops/build-tlds.py.\n{body}\n",
        encoding="utf-8",
    )
    print(f"{DST}: {len(tlds)} доменов")


if __name__ == "__main__":
    main()
