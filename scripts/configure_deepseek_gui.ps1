$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = "Pangu Tianji - DeepSeek API"
$form.StartPosition = "CenterScreen"
$form.ClientSize = New-Object System.Drawing.Size(520, 190)
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.TopMost = $true

$title = New-Object System.Windows.Forms.Label
$title.Text = "Enter your DeepSeek API key"
$title.Font = New-Object System.Drawing.Font("Segoe UI", 12, [System.Drawing.FontStyle]::Bold)
$title.Location = New-Object System.Drawing.Point(24, 20)
$title.AutoSize = $true
$form.Controls.Add($title)

$note = New-Object System.Windows.Forms.Label
$note.Text = "The key stays hidden and is saved only to your Windows user environment."
$note.Location = New-Object System.Drawing.Point(24, 54)
$note.Size = New-Object System.Drawing.Size(470, 24)
$form.Controls.Add($note)

$keyBox = New-Object System.Windows.Forms.TextBox
$keyBox.Location = New-Object System.Drawing.Point(24, 84)
$keyBox.Size = New-Object System.Drawing.Size(470, 27)
$keyBox.UseSystemPasswordChar = $true
$form.Controls.Add($keyBox)

$saveButton = New-Object System.Windows.Forms.Button
$saveButton.Text = "Save"
$saveButton.Location = New-Object System.Drawing.Point(319, 132)
$saveButton.Size = New-Object System.Drawing.Size(82, 32)
$form.Controls.Add($saveButton)

$cancelButton = New-Object System.Windows.Forms.Button
$cancelButton.Text = "Cancel"
$cancelButton.Location = New-Object System.Drawing.Point(412, 132)
$cancelButton.Size = New-Object System.Drawing.Size(82, 32)
$form.Controls.Add($cancelButton)

$saveButton.Add_Click({
    $apiKey = $keyBox.Text.Trim()
    if ([string]::IsNullOrWhiteSpace($apiKey) -or -not $apiKey.StartsWith("sk-")) {
        [System.Windows.Forms.MessageBox]::Show(
            "The API key is empty or does not start with sk-.",
            "Invalid API key",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Warning
        ) | Out-Null
        return
    }

    try {
        # Keep provider secrets outside project files and command history.
        [Environment]::SetEnvironmentVariable("ASHARE_MODEL_PROVIDER", "openai_compatible", "User")
        [Environment]::SetEnvironmentVariable("ASHARE_MODEL_BASE_URL", "https://api.deepseek.com", "User")
        [Environment]::SetEnvironmentVariable("ASHARE_MODEL_API_KEY", $apiKey, "User")
        [Environment]::SetEnvironmentVariable("ASHARE_MODEL_NAME", "deepseek-v4-flash", "User")
        [Environment]::SetEnvironmentVariable("ASHARE_MODEL_HEALTH_PATH", "/models", "User")
        $keyBox.Clear()
        $apiKey = $null
        [System.Windows.Forms.MessageBox]::Show(
            "Saved successfully. Restart the Pangu Tianji dashboard next.",
            "DeepSeek configured",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information
        ) | Out-Null
        $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $form.Close()
    }
    catch {
        $keyBox.Clear()
        $apiKey = $null
        [System.Windows.Forms.MessageBox]::Show(
            "Could not save the settings: $($_.Exception.Message)",
            "DeepSeek setup failed",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
    }
})

$cancelButton.Add_Click({
    $keyBox.Clear()
    $form.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $form.Close()
})

$form.Add_Shown({ $keyBox.Focus() })
$result = $form.ShowDialog()
$keyBox.Clear()
$form.Dispose()

if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
    exit 1
}

