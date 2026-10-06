import os
import tempfile

# База — во временном файле, ДО импорта приложения (store читает env при импорте).
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="interview-test-"), "interview.db")
os.environ["INTERVIEW_DB"] = _TMP_DB
