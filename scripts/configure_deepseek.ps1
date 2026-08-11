$ErrorActionPreference = "Stop"

Write-Host "Pangu Tianji - DeepSeek setup" -ForegroundColor Cyan
Write-Host "Paste the DeepSeek API key below. The input will stay hidden."
$secureKey = Read-Host "DeepSeek API Key" -AsSecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)

try {
    $apiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    if ([string]::IsNullOrWhiteSpace($apiKey) -or -not $apiKey.StartsWith("sk-")) {
        throw "The API key is empty or does not start with sk-."
    }

    # Keep provider secrets outside project files and command history.
    [Environment]::SetEnvironmentVariable("ASHARE_MODEL_PROVIDER", "openai_compatible", "User")
    [Environment]::SetEnvironmentVariable("ASHARE_MODEL_BASE_URL", "https://api.deepseek.com", "User")
    [Environment]::SetEnvironmentVariable("ASHARE_MODEL_API_KEY", $apiKey, "User")
    [Environment]::SetEnvironmentVariable("ASHARE_MODEL_NAME", "deepseek-v4-flash", "User")
    [Environment]::SetEnvironmentVariable("ASHARE_MODEL_HEALTH_PATH", "/models", "User")

    Write-Host "DeepSeek environment variables were saved for the current Windows user." -ForegroundColor Green
}
finally {
    if ($keyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
    $apiKey = $null
    $secureKey = $null
}

