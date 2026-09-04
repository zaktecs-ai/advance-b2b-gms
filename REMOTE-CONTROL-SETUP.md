# 🌟 Cline Agent — Remote Control (Phone / Laptop se GitHub Actions)

Ab app apne **mobile** se (ya kahin se bhi) GitHub pe `@cline` mention karke
**Cline Agent** ko kaam de sakte ho — **laptop band ho to bhi chalta hai!**
Kaam GitHub ki cloud machine (GitHub Actions) pe hota hai.

---

## Ye chala raha kya hai?

- **`@cline`** → jab tum kisi **Issue** me `@cline` likhoge
- GitHub Actions cloud machine (`ubuntu-latest`) me Cline CLI chalega
- Agent tumhare saved **Cline free-tier login** se authenticate hoga
- Task complete → code **nayi branch** me push → **PR banta hai** → result
  issue par reply hota hai

## Setup — bas ek baar karna hai

### Step 1 — Files ko GitHub Secrets me daalo (zaroori)
Ye 2 files tumhare login hain:
- `secrets.json`
- `globalState.json`

Inhe **kabhi repo me commit nahi karna**. Inki base64 value `.cline-setup/`
folder me ready hai. GitHub pe:

1. Repo kholo → **Settings → Secrets and variables → Actions**
2. **New repository secret** banao:
   - Name: `CLINE_SECRETS_B64` → Value: `.cline-setup/secrets.b64` ka poora content
   - Name: `CLINE_GLOBALSTATE_B64` → Value: `.cline-setup/globalState.b64` ka poora content

### Step 2 — (Optional) Model choose karo
Default: `deepseek/deepseek-v4-flash` (free).
Badalna ho to: Repo **Settings → Variables → Actions** me
`CLINE_MODEL` naam se koi bhi Cline free model daalo
(e.g. `z-ai/glm-5.3-flash`).

### Step 3 — Workflow push karo
`.github/workflows/cline-responder.yml` repo me commit karke push karo
(default branch, i.e. `main`).

---

## 🚀 Kaise use karo (roz ka use)

1. Repo me **New Issue** kholo
2. Issue ki title/body me likho jo kama karna hai (Hinglish/English dono chalega):
   > `@cline ek todo-list app bana do Python Flask se, README ke saath`
3. **Comment** me `@cline` mention karo aur task likho
4. GitHub Actions shuru hoga → kaam ho jayega → **PR ready** milega

Mobile se: **GitHub app** me issue/comment banate jaana — kaafi hain.

## 🔒 Security — im portant

- Workflow **sirf repo owner (ya `CLINE_ALLOWED_USERS` me listed) ko**
  trigger karta hai. Doosre kisi ke `@cline` se agent nahi chalega.
- `secrets.json` / `globalState.json` **gitignore** me already hai —
  acciden tal commit nahi honge.
- GitHub Actions minutes: private repo me Pro pe **3000/month**, public repo
  me **unlimited**. Naam ke liye kisi bhi repo me chalaya ja sakta hai.

## 💡 Tip — Naya project/repo banana

Agent se naye project folder bannwa sakte ho:
> `@cline ek naya project 'portfolio site' bana do (HTML/CSS/JS), projects/portfolio/ folder me`

Colourful push ho jayega. Naye GitHub repo ko direct create karwana alag
setup hai — pehle ye workflow chalte dekho, phir chaaho to badalenge.

---

## 🔧 Troubleshooting

| Problem | Fix |
|---------|-----|
| Action "Restore credentials" me file nahi | Secrets set nahi kiye → Step 1 dobara |
| Agent auth error (401/Unauthorized) | Cline login ka refreshToken expire ho gaya → `cline auth` se naya session banao, `secrets.json` + `globalState.json` ko `.cline-setup/` me dobara base64 karo, secrets update karo |
| Free quota khatam | Din ke free models use ho gaye → agle din try karo, ya `CLINE_MODEL` badlo |
| PR nahi bana | Sirf analysis/reply, koi code change nahi hua — result me bataya jayega |