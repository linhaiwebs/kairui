$c = Get-Content "C:\Users\Administrator\Desktop\kairui\frontend\static\js\app-st212428.js" -Encoding UTF8 -Raw | Out-String
$tplStart = $c.IndexOf("template:")
$tpl = $c.Substring($tplStart + 9)
$bt = "`"
$tpl = $tpl.Substring($tpl.IndexOf($bt) + 1)
$tpl = $tpl.Substring(0, $tpl.LastIndexOf($bt))

$tags = @("div","span","nav","header","main","section","button","a","form","label","table","thead","tbody","tr","th","td","ul","li","ol","p","h1","h2","h3","h4","h5","select","option","textarea","strong","em","small","i","b","article","aside","footer")

foreach ($tag in $tags) {
    $open = ([regex]::Matches($tpl, "<$tag[\s>]")).Count
    $close = ([regex]::Matches($tpl, "</$tag>")).Count
    if ($open -ne $close) {
        Write-Host "IMBALANCE: $tag open=$open close=$close diff=$($open - $close)"
    }
}
Write-Host "Scan complete. tpl length: $($tpl.Length)"
