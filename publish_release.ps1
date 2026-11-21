param([switch]$Publish)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$releaseVersion = python -c "from version import VERSION; print(VERSION)"
$releaseRepo = python -c "from version import REPOSITORY; print(REPOSITORY)"
$releaseName = "WebP-Tool-v$releaseVersion.exe"
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
python build.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
python verify_packaged.py
if ($LASTEXITCODE -ne 0) { throw 'Packaged verification failed' }
python create_manifest.py
if ($LASTEXITCODE -ne 0) { throw 'Manifest generation failed' }
if ($Publish) {
    gh release create "v$releaseVersion" "release/$releaseName" 'release/latest.json' `
        --repo $releaseRepo --title "v$releaseVersion" --notes-file RELEASE_NOTES.md --verify-tag
    if ($LASTEXITCODE -ne 0) { throw 'Publish failed' }
} else {
    Write-Host 'Build verified. Commit and push a matching version tag, then use -Publish to publish.'
}
