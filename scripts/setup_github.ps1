<#
  One-time setup: puts Walang Pasok on GitHub and starts the watcher.

  Run from the project folder in PowerShell:
      powershell -ExecutionPolicy Bypass -File scripts\setup_github.ps1

  It will:
    1. copy the GitHub Actions workflows into .github\workflows
    2. sign you in to GitHub (browser) if needed
    3. commit the project and create + push the GitHub repo
    4. ask for your Neon database address and save it as the DATABASE_URL secret
       (typed into GitHub CLI directly; it is never shown or saved in the project)
    5. start the first watcher run

  Safe to run again: steps that are already done are skipped.
#>
# "Continue", not "Stop": Windows PowerShell 5.1 treats any stderr line from git/gh
# (even harmless ones) as a fatal error under "Stop". Exit codes are checked instead.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host $text -ForegroundColor Red; exit 1 }
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

# --- Tools -------------------------------------------------------------------
if (-not (Have git)) { Write-Host "Git is not installed. Install it with:  winget install --id Git.Git -e" -ForegroundColor Red; exit 1 }
if (-not (Have gh)) {
    Write-Host "GitHub CLI is not installed. Install it with:  winget install --id GitHub.cli -e" -ForegroundColor Yellow
    Write-Host "Then close and reopen this terminal and run this script again."
    exit 1
}

# --- 1. Workflows ------------------------------------------------------------
Step "Adding the GitHub Actions workflows"
New-Item -ItemType Directory -Force ".github\workflows" | Out-Null
Copy-Item "deploy\github-workflows\*.yml" ".github\workflows\" -Force
Get-ChildItem ".github\workflows" | ForEach-Object { Write-Host "  $($_.Name)" }

# --- 2. GitHub sign-in -------------------------------------------------------
Step "Checking GitHub sign-in"
gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "A browser window will open to sign in to GitHub."
    # 'workflow' scope is needed to push files in .github/workflows
    gh auth login --web --git-protocol https --scopes workflow
}
else {
    # Pushing files in .github/workflows needs the 'workflow' permission.
    $status = gh auth status 2>&1 | Out-String
    if ($status -notmatch "workflow") { gh auth refresh --scopes workflow }
}
gh auth status *> $null
if ($LASTEXITCODE -ne 0) { Fail "GitHub sign-in didn't finish. Run the script again." }
gh auth setup-git *> $null
$login = (gh api user --jq .login).Trim()
Write-Host "  Signed in as $login"

# --- 3. Commit ---------------------------------------------------------------
Step "Committing the project"
if (-not (git config user.name)) {
    $id = (gh api user --jq .id).Trim()
    git config user.name $login
    git config user.email "$id+$login@users.noreply.github.com"
}
git add -A
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m "Walang Pasok: API, website, alerts, importer and news watcher" | Out-Null
    Write-Host "  Committed."
} else {
    Write-Host "  Nothing new to commit."
}
git branch -M main

# --- 4. Repo -----------------------------------------------------------------
Step "Creating the GitHub repository"
git remote get-url origin *> $null
if ($LASTEXITCODE -ne 0) {
    $name = Read-Host "Repository name [walang-pasok-api]"
    if (-not $name) { $name = "walang-pasok-api" }
    Write-Host "  Public, because GitHub Actions is free and unlimited for public repos."
    Write-Host "  (A private repo's free minutes would run out with a check every 15 minutes.)"
    Write-Host "  Your .env file and database are in .gitignore, so they are NOT uploaded."
    gh repo create $name --public --source . --remote origin --push --description "Class suspensions in the Philippines, verified and searchable by city."
    if ($LASTEXITCODE -ne 0) { Fail "Creating the repo failed (is the name already taken?). Run again with another name." }
} else {
    git push -u origin main
    if ($LASTEXITCODE -ne 0) { Fail "Push failed. See the message above." }
}
$repoUrl = (gh repo view --json url --jq .url).Trim()
Write-Host "  $repoUrl"

# --- 5. Database secret ------------------------------------------------------
Step "Connecting the watcher to your database"
$existing = gh secret list --json name --jq ".[].name" 2>$null
if ($existing -notcontains "DATABASE_URL") {
    Write-Host "Create a free Postgres database at https://neon.tech, then copy its connection string"
    Write-Host "(it starts with postgresql://). Paste it below; nothing will show while you paste."
    gh secret set DATABASE_URL
    if ($LASTEXITCODE -ne 0) { Fail "Saving the secret failed. Run the script again." }
} else {
    Write-Host "  DATABASE_URL is already set."
}
$hook = Read-Host "Optional: a private Discord webhook URL for 'new pending' reports (Enter to skip)"
if ($hook) { $hook | gh secret set ADMIN_DISCORD_WEBHOOK_URL }

# --- 6. GitHub Pages -----------------------------------------------------------
Step "Turning on GitHub Pages (deployed by GitHub Actions)"
$repoPath = (gh repo view --json nameWithOwner --jq .nameWithOwner).Trim()
gh api "repos/$repoPath/pages" *> $null
if ($LASTEXITCODE -ne 0) {
    gh api -X POST "repos/$repoPath/pages" -f build_type=workflow *> $null
    if ($LASTEXITCODE -ne 0) { Write-Host "  Turn it on by hand: Settings > Pages > Source: GitHub Actions" -ForegroundColor Yellow }
} else {
    gh api -X PUT "repos/$repoPath/pages" -f build_type=workflow *> $null
}
$pagesUrl = (gh api "repos/$repoPath/pages" --jq .html_url 2>$null)

# --- 7. First run ------------------------------------------------------------
Step "Starting the first run (check the news, build and publish the site)"
Start-Sleep -Seconds 3
gh workflow run site.yml -f load_examples=true
if ($LASTEXITCODE -ne 0) {
    Write-Host "  GitHub may need a moment to see the new workflow. Start it from the Actions tab:" -ForegroundColor Yellow
    Write-Host "  Actions > Update site > Run workflow"
}
Write-Host "  Watch it live:  $repoUrl/actions"
if ($pagesUrl) { Write-Host "  Your site:      $pagesUrl  (live after the first run, about 3 minutes)" }
Write-Host "  From now on it runs by itself every 15 minutes."
Write-Host "  Admin tasks (verify, import an LGU post): Actions > Admin > Run workflow"
Write-Host "`nDone." -ForegroundColor Green
