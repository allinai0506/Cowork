set uid to do shell script "id -u"
do shell script "launchctl kickstart -k gui/" & uid & "/com.user.herdr-factory-console >/dev/null 2>&1 || true"
delay 1
open location "http://127.0.0.1:8765"
