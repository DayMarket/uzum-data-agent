import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "lib"))


class _TelemetryQueue(object):
    """Телеметрия, направленная не в сеть, а в локальную очередь на диске.

    ClickHouse указан заведомо недоступным (`127.0.0.1:1` — мгновенный отказ
    соединения), после чего lib/telemetry.write() кладёт строку файлом в
    очередь. Так «хук записал строку» и «хук не записал ничего» становятся
    проверяемыми фактами, а не догадками по пустому stdout: без этого тест на
    границу «наша сессия / чужая» проходил бы и на коде, который не пишет
    телеметрию вообще.
    """

    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    @property
    def env(self):
        return {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.state_dir),
            "UZUM_STATE_DIR": str(self.state_dir),
            "TELEMETRY_CH_HOST": "127.0.0.1",
            "TELEMETRY_CH_PORT": "1",
        }

    def rows(self):
        """[(таблица, строка), …] — всё, что накопилось в очереди."""
        queue = self.state_dir / "queue"
        if not queue.exists():
            return []
        out = []
        for path in sorted(queue.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                out.append((item.get("table"), item.get("row", item)))
        return out


@pytest.fixture
def telemetry_queue(tmp_path):
    return _TelemetryQueue(tmp_path / "state")
