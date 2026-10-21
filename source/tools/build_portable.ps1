param(
    [Parameter(Mandatory=$true)][string]$SourceDirectory,
    [Parameter(Mandatory=$true)][string]$ArtifactDirectory,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9._-]{1,80}$')][string]$BuildId
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$sourcePath = (Resolve-Path -LiteralPath $SourceDirectory).Path
$artifactPath = (Resolve-Path -LiteralPath $ArtifactDirectory).Path
$outputPath = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $outputPath) { throw 'Build output already exists; choose a new Build ID/directory.' }
$utf8 = New-Object Text.UTF8Encoding($false)
$lock = Get-Content -LiteralPath (Join-Path $sourcePath 'RUNTIME_SOURCE_LOCK.json') -Raw | ConvertFrom-Json

function Get-BytesSha([byte[]]$value) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($value)).Replace('-','') }
    finally { $sha.Dispose() }
}
function Assert-Artifact($row, [string]$path) {
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw ('Missing artifact: '+$row.filename) }
    $item = Get-Item -LiteralPath $path
    if ($item.Length -ne $row.bytes -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $row.sha256) {
        throw ('Artifact hash/size mismatch: '+$row.filename)
    }
}
function Expand-Safe([string]$archivePath, [string]$destination) {
    $prefix = [IO.Path]::GetFullPath($destination).TrimEnd('\')+'\'
    $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        foreach ($entry in $archive.Entries) {
            $name = $entry.FullName.Replace('/','\')
            if ($name.Contains(':') -or [IO.Path]::IsPathRooted($name) -or $name.Split('\') -contains '..') { throw 'Unsafe ZIP member' }
            if (($entry.ExternalAttributes -band 0xF0000000) -eq 0xA0000000) { throw 'Archive symlink not permitted' }
            $target = [IO.Path]::GetFullPath((Join-Path $destination $name))
            if (!$target.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'ZIP traversal' }
            if ($entry.FullName.EndsWith('/')) { [IO.Directory]::CreateDirectory($target) | Out-Null; continue }
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
            if (Test-Path -LiteralPath $target) { throw ('ZIP collision: '+$name) }
            $input = $entry.Open()
            $output = [IO.File]::Open($target,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
            try { $input.CopyTo($output) } finally { $output.Dispose(); $input.Dispose() }
        }
    } finally { $archive.Dispose() }
}
function Get-Inventory([string]$directory) {
    $map = @{}
    foreach ($file in Get-ChildItem -LiteralPath $directory -Recurse -File) {
        if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse member not permitted' }
        $name = $file.FullName.Substring($directory.TrimEnd('\').Length+1).Replace('\','/')
        $map[$name] = [ordered]@{path=$name;bytes=$file.Length;sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash}
    }
    [string[]]$names = @($map.Keys)
    [Array]::Sort($names,[StringComparer]::Ordinal)
    return @($names | ForEach-Object { $map[$_] })
}
function Get-Aggregate($rows) {
    $lines = New-Object Text.StringBuilder
    foreach ($row in $rows) { [void]$lines.Append($row.path).Append([char]9).Append($row.bytes).Append([char]9).Append($row.sha256).Append([char]10) }
    return Get-BytesSha $utf8.GetBytes($lines.ToString())
}
$runtimeZip = Join-Path $artifactPath $lock.runtime.filename
Assert-Artifact $lock.runtime $runtimeZip
foreach ($wheel in $lock.wheels) { Assert-Artifact $wheel (Join-Path $artifactPath ('wheels\'+$wheel.filename)) }
[IO.Directory]::CreateDirectory($outputPath) | Out-Null
$usePath = Join-Path $outputPath 'Windows'
$srcPath = Join-Path $outputPath 'Source'
[IO.Directory]::CreateDirectory($usePath) | Out-Null
[IO.Directory]::CreateDirectory($srcPath) | Out-Null
$sourceMembers = @('.gitignore','LICENSE','README.md','THIRD_PARTY_NOTICES.md','requirements-demo.txt','requirements-runtime-with-hashes.txt','requirements-test-only-with-hashes.txt','RUNTIME_SOURCE_LOCK.json','START_DEMO.cmd','tools/portable_launcher.py','tools/console_commands.py','tools/portable_worker.py','tools/native_supervisor.py','tools/review_adapter.py','tools/export_review.py','tools/build_portable.ps1','tests/control_probe.py','tests/test_backend.py','tests/test_frontend.js','tests/test_package.py','tests/test_new_package_faults.py','tests/test_safe_receipts.py','tests/test_lifecycle.py','tests/test_console_commands.py','tests/test_review_integration.py','docs/SAFE_RECEIPT_API.md','docs/SAFE_RECEIPT_SCHEMA.json','docs/BUILDING.md','docs/TESTING.md','EXPORT_REVIEW.cmd')
foreach ($appFile in Get-ChildItem -LiteralPath (Join-Path $sourcePath 'app') -Recurse -File) {
    if ($appFile.FullName.Contains('__pycache__')) { continue }
    $sourceMembers += $appFile.FullName.Substring($sourcePath.Length+1).Replace('\','/')
}
foreach ($relative in $sourceMembers) {
    $target = Join-Path $srcPath $relative
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourcePath $relative) -Destination $target
    if (!$relative.StartsWith('tests/') -and $relative -notin @('requirements-test-only-with-hashes.txt','.gitignore') -and $relative -notin @('tools/build_portable.ps1')) {
        $useTarget = Join-Path $usePath $relative
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($useTarget)) | Out-Null
        Copy-Item -LiteralPath (Join-Path $sourcePath $relative) -Destination $useTarget
    }
}
$sourceRows = Get-Inventory $srcPath
$sourceAggregate = Get-Aggregate $sourceRows
$appRows = @($sourceRows | Where-Object { $_.path.StartsWith('app/') })
$info = [ordered]@{build_id=$BuildId; source_aggregate_sha256=$sourceAggregate; app_aggregate_sha256=(Get-Aggregate $appRows); platform='Windows 11 x64'; acceptance='PENDING'; format='ordinal path TAB bytes TAB uppercase SHA LF'; files=$sourceRows}
[IO.File]::WriteAllText((Join-Path $srcPath 'SOURCE_MANIFEST.json'),($info | ConvertTo-Json -Depth 12),$utf8)
$runtimePath = Join-Path $usePath 'runtime'
[IO.Directory]::CreateDirectory($runtimePath) | Out-Null
Expand-Safe $runtimeZip $runtimePath
$sitePath = Join-Path $runtimePath 'Lib\site-packages'
[IO.Directory]::CreateDirectory($sitePath) | Out-Null
foreach ($wheel in $lock.wheels) { Expand-Safe (Join-Path $artifactPath ('wheels\'+$wheel.filename)) $sitePath }
$pthPath = Join-Path $runtimePath 'python312._pth'
$beforePth = [IO.File]::ReadAllBytes($pthPath)
$expectedPth = $utf8.GetBytes("python312.zip"+[char]10+"."+[char]10+"Lib/site-packages"+[char]10+".."+[char]10)
[IO.File]::WriteAllBytes((Join-Path $outputPath 'python312._pth.before'),$beforePth)
$pthIntent = [ordered]@{path='Windows/runtime/python312._pth';OLD_SHA256=(Get-BytesSha $beforePth);OLD_BYTES=$beforePth.Length;EXPECTED_SHA256=(Get-BytesSha $expectedPth);EXPECTED_BYTES=$expectedPth.Length;backup='python312._pth.before'}
[IO.File]::WriteAllText((Join-Path $outputPath 'PTH_WRITE_EXPECTED.json'),($pthIntent | ConvertTo-Json),$utf8)
[IO.File]::WriteAllBytes($pthPath,$expectedPth)
if ((Get-FileHash -LiteralPath $pthPath -Algorithm SHA256).Hash -ne $pthIntent.EXPECTED_SHA256) { throw 'Runtime path write mismatch' }
$manifest = [ordered]@{build_id=$BuildId;source_aggregate_sha256=$sourceAggregate;app_aggregate_sha256=$info.app_aggregate_sha256;files=(Get-Inventory $usePath)}
[IO.File]::WriteAllText((Join-Path $usePath 'PACKAGE_INTEGRITY.json'),($manifest | ConvertTo-Json -Depth 12),$utf8)
[IO.Compression.ZipFile]::CreateFromDirectory($usePath,(Join-Path $outputPath 'HO_V2_Windows_x64.zip'),[IO.Compression.CompressionLevel]::Optimal,$false)
[IO.Compression.ZipFile]::CreateFromDirectory($srcPath,(Join-Path $outputPath 'HO_V2_Source.zip'),[IO.Compression.CompressionLevel]::Optimal,$false)
$summary = [ordered]@{build_id=$BuildId;source_aggregate_sha256=$sourceAggregate;app_aggregate_sha256=$info.app_aggregate_sha256;source_members=$sourceRows.Count;runtime_files=$manifest.files.Count;status='BUILT_NOT_RUN';archives=@()}
foreach ($name in @('HO_V2_Windows_x64.zip','HO_V2_Source.zip')) {
    $file = Get-Item -LiteralPath (Join-Path $outputPath $name)
    $summary.archives += [ordered]@{path=$name;bytes=$file.Length;sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash}
}
[IO.File]::WriteAllText((Join-Path $outputPath 'BUILD_RESULT.json'),($summary | ConvertTo-Json -Depth 12),$utf8)
$summary | ConvertTo-Json -Depth 12
