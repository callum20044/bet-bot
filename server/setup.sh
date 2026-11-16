#!/usr/bin/env bash
# Round Table server setup (Oracle Cloud Always Free, UK South / London). Run once:
#   curl -fsSL https://raw.githubusercontent.com/callum20044/bet-bot/main/server/setup.sh | sudo bash
# It does two jobs and holds NO trading code or betting passwords:
#   1. A UK "tunnel" for Betfair: GitHub's US machines connect in over SSH and reach ONLY Betfair's
#      login/API addresses through it (Betfair blocks US addresses). The tunnel user has no shell.
#   2. A reliable clock: it starts the bots' GitHub workflows on time (GitHub's own timer runs late).
# The only secret it asks for is a GitHub token (fine-grained: Actions + Secrets read/write on
# bet-bot and swing-bot). It is stored root-only in /etc/roundtable/token and never printed.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
OWNER=callum20044
echo "== installing tools"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl jq openssh-server >/dev/null
if ! command -v gh >/dev/null; then
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list
  apt-get update -qq && apt-get install -y -qq gh >/dev/null
fi
timedatectl set-timezone Europe/London || true   # the T212 bots work to UK-time windows

echo "== GitHub token"
mkdir -p /etc/roundtable && chmod 700 /etc/roundtable
if [ ! -s /etc/roundtable/token ]; then
  read -rsp "Paste your GitHub token, then press Enter (it won't show): " TOKEN </dev/tty; echo
  printf '%s' "$TOKEN" > /etc/roundtable/token; chmod 600 /etc/roundtable/token
fi
export GH_TOKEN="$(cat /etc/roundtable/token)"
for r in bet-bot swing-bot; do
  gh api "repos/$OWNER/$r" --jq .full_name >/dev/null || { echo "token can't see $r - check its repository access"; rm -f /etc/roundtable/token; exit 1; }
done
echo "token OK"

echo "== Betfair tunnel (ssh, Betfair addresses only, no shell)"
id rt >/dev/null 2>&1 || useradd -m -s /usr/sbin/nologin rt
install -d -m 700 -o rt -g rt /home/rt/.ssh
if [ ! -s /etc/roundtable/tunnel_key ]; then
  ssh-keygen -q -t rsa -b 4096 -N "" -C "github-betfair-tunnel" -f /etc/roundtable/tunnel_key
fi
echo "restrict,port-forwarding,command=\"/bin/false\" $(cat /etc/roundtable/tunnel_key.pub)" > /home/rt/.ssh/authorized_keys
chown rt:rt /home/rt/.ssh/authorized_keys; chmod 600 /home/rt/.ssh/authorized_keys
cat > /etc/ssh/sshd_config.d/90-roundtable.conf <<'CONF'
Match User rt
    AllowTcpForwarding local
    PermitOpen identitysso.betfair.com:443 identitysso-cert.betfair.com:443 api.betfair.com:443
    X11Forwarding no
    AllowAgentForwarding no
    PermitTTY no
    ForceCommand /bin/false
CONF
sshd -t && (systemctl reload ssh || systemctl reload sshd)
IP="$(curl -fsS https://api.ipify.org)"
gh secret set BETFAIR_SSH_HOST -R "$OWNER/bet-bot" --body "$IP" >/dev/null
gh secret set BETFAIR_SSH_KEY -R "$OWNER/bet-bot" < /etc/roundtable/tunnel_key >/dev/null
gh secret set BETFAIR_SSH_HOSTKEY -R "$OWNER/bet-bot" --body "$(ssh-keyscan -t rsa,ecdsa,ed25519 "$IP" 2>/dev/null)" >/dev/null
echo "tunnel secrets saved to GitHub (bet-bot)"

echo "== clock: start the bots on time"
cat > /opt/roundtable-run <<'RUN'
#!/usr/bin/env bash
# start one GitHub workflow (log times are UK time): roundtable-run <repo> <workflow file> [input=value ...]
export GH_TOKEN="$(cat /etc/roundtable/token)"
repo="$1"; wf="$2"; shift 2
args=(); for kv in "$@"; do args+=(-f "$kv"); done
if out=$(gh workflow run "$wf" -R "callum20044/$repo" "${args[@]}" 2>&1); then
  echo "$(date +%FT%H:%M) started $repo/$wf $*" >> /var/log/roundtable.log
else
  echo "$(date +%FT%H:%M) FAILED $repo/$wf: $(echo "$out" | tr '\n' ' ' | cut -c1-200)" >> /var/log/roundtable.log
fi
RUN
chmod 755 /opt/roundtable-run
cat > /etc/cron.d/roundtable <<'CRON'
# All times UK time (server clock is Europe/London). GitHub's own timers stay on as a backup; the workflows queue rather than overlap.
SHELL=/bin/bash
*/15 * * * *   root /opt/roundtable-run bet-bot scan.yml
7 * * * 1-5    root /opt/roundtable-run swing-bot cfd-trader.yml
7 22,23 * * 0  root /opt/roundtable-run swing-bot cfd-trader.yml
17 */2 * * *   root /opt/roundtable-run swing-bot deals.yml
40 14 * * 1-5  root /opt/roundtable-run swing-bot swing-bot.yml test_run=false
40 15 * * 1-5  root /opt/roundtable-run swing-bot swing-bot.yml test_run=false
25 9,13 * * 1-5 root /opt/roundtable-run swing-bot gold-bot.yml test_run=false
23 6 * * *     root /opt/roundtable-run swing-bot cfd-factory.yml
41 6 * * *     root /opt/roundtable-run swing-bot funding.yml
17 16 * * *    root /opt/roundtable-run bet-bot gaps.yml
30 7 * * 1,4   root /opt/roundtable-run bet-bot shorts.yml
CRON
chmod 644 /etc/cron.d/roundtable
systemctl restart cron
echo
echo "ALL DONE - server $IP is set up. Test-starting one bot in each repo..."
/opt/roundtable-run bet-bot scan.yml; /opt/roundtable-run swing-bot deals.yml; tail -2 /var/log/roundtable.log
