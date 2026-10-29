// Reads every Blueprint type from a game's packages (offline, with CUE4Parse) and writes one JSON line per type,
// for usmap_offline.py, which turns them into a mapping file (.usmap).
//
// Why: the running game holds only the Blueprint types that are loaded at that moment. The packages hold all of
// them: each Blueprint class, user defined struct and user defined enum is an export with its properties in a
// self-describing form, so a mapping file with the game's native types is enough to read them.
//
// Usage: usmap_offline <Paks folder> <AES key 0x...> <native .usmap> <oo2core dll> <out.jsonl>
//                      [--game GAME_UE5_7] [--limit N] [--threads N]
// The JSON line of a type: {"pkg", "name", "cls", "kind" (1 struct, 2 class, 3 enum), "super", "flags",
// "objflags", "props": [{"n", "d", "f", "t"}]} or for an enum {"entries": [[value, name], ...]}. A property type
// "t" is {"k": serialized type name, "enum"|"struct"|"inner"|"key"|"value"|"under": ...}. Errors go to
// <out>.errors.txt, counts to the console.

using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;
using CUE4Parse.Compression;
using CUE4Parse.Encryption.Aes;
using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Objects.Core.Misc;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Versions;

if (args.Length < 5)
{
    Console.Error.WriteLine("usage: usmap_offline <Paks folder> <AES key> <native .usmap> <oo2core dll> <out.jsonl> [--game GAME_UE5_7] [--limit N] [--threads N]");
    return 2;
}
string paks = args[0], aes = args[1], usmap = args[2], oodle = args[3], outPath = args[4];
var game = EGame.GAME_UE5_7;
int limit = int.MaxValue, threads = Environment.ProcessorCount;
for (int i = 5; i < args.Length - 1; i++)
{
    if (args[i] == "--game") game = Enum.Parse<EGame>(args[i + 1]);
    if (args[i] == "--limit") limit = int.Parse(args[i + 1]);
    if (args[i] == "--threads") threads = int.Parse(args[i + 1]);
}

var clock = Stopwatch.StartNew();
OodleHelper.Initialize(oodle);
var provider = new DefaultFileProvider(paks, SearchOption.TopDirectoryOnly, new VersionContainer(game));
provider.MappingsContainer = new FileUsmapTypeMappingsProvider(usmap);
provider.Initialize();
provider.SubmitKey(new FGuid(), new FAesKey(aes));
provider.PostMount();
string probe = args.SkipWhile(a => a != "--probe").Skip(1).FirstOrDefault();
if (probe != null)
{
    // --probe <text>: print the exports and their classes of the packages whose path holds <text>, then stop.
    foreach (var f in provider.Files.Values.Where(f => f.Extension == "uasset" && f.Path.Contains(probe, StringComparison.OrdinalIgnoreCase)).Take(limit == int.MaxValue ? 5 : limit))
    {
        var p = (IoPackage)provider.LoadPackage(f);
        Console.WriteLine(p.Name);
        foreach (var e in p.ExportMap)
            Console.WriteLine($"    {p.CreateFNameFromMappedName(e.ObjectName).Text}  ({p.ResolveObjectIndex(e.ClassIndex)?.Name.Text})");
    }
    return 0;
}
int readTest = int.Parse(args.SkipWhile(a => a != "--readtest").Skip(1).FirstOrDefault() ?? "0");
if (readTest > 0)
{
    // --readtest <step>: read every export of every <step>-th gameplay package (BP_, DT_, DA_, PDA_, W_, WB_, GA_,
    // GE_, BD_, AM_) with the given mapping file; count the exports that fail and the warnings CUE4Parse logs
    // (for example properties it could not read).
    string[] prefixes = ["BP_", "DT_", "DA_", "PDA_", "W_", "WB_", "GA_", "GE_", "BD_", "AM_"];
    var sample = provider.Files.Values
        .Where(f => f.Extension == "uasset" && prefixes.Any(p => f.Name.StartsWith(p, StringComparison.OrdinalIgnoreCase)))
        .OrderBy(f => f.Path, StringComparer.Ordinal).Where((_, i) => i % readTest == 0).ToList();
    var sink = new CountingSink();
    Serilog.Log.Logger = new Serilog.LoggerConfiguration().MinimumLevel.Warning().WriteTo.Sink(sink).CreateLogger();
    int exports = 0, failed = 0;
    var failures = new ConcurrentQueue<string>();
    Parallel.ForEach(sample, new ParallelOptions { MaxDegreeOfParallelism = threads }, f =>
    {
        try
        {
            var p = provider.LoadPackage(f);
            for (int i = 0; i < p.ExportsLazy.Length; i++)
            {
                Interlocked.Increment(ref exports);
                try { _ = p.ExportsLazy[i].Value; }
                catch (Exception ex) { Interlocked.Increment(ref failed); failures.Enqueue($"{f.Path} #{i}: {ex.GetType().Name}: {ex.Message}"); }
            }
        }
        catch (Exception ex) { Interlocked.Increment(ref failed); failures.Enqueue($"{f.Path}: {ex.GetType().Name}: {ex.Message}"); }
    });
    Console.WriteLine($"read test with {Path.GetFileName(usmap)}: {sample.Count} packages, {exports} exports, {failed} failed, {sink.Count} warnings ({clock.Elapsed.TotalSeconds:F0} s)");
    File.WriteAllLines(outPath, failures.Concat(sink.Examples));
    return 0;
}
var files = provider.Files.Values.Where(f => f.Extension is "uasset" or "umap").Take(limit).ToList();
Console.WriteLine($"{provider.Files.Count} files mounted, {files.Count} packages to read ({clock.Elapsed.TotalSeconds:F0} s)");

var lines = new ConcurrentQueue<string>();
var errors = new ConcurrentQueue<string>();
var classCounts = new ConcurrentDictionary<string, int>();
int done = 0, packageErrors = 0, exportErrors = 0;

static bool Wanted(string cls) =>
    cls.EndsWith("Class") || cls.EndsWith("Struct") || cls.EndsWith("Enum");

static string Name(FPackageIndex index) =>
    index == null || index.IsNull ? null : index.ResolvedObject?.Name.Text ?? "?unresolved";

object TypeOf(FProperty p)
{
    if (p == null) return new Dictionary<string, object> { ["k"] = "?missing" };
    var t = new Dictionary<string, object> { ["k"] = p.GetType().Name.Substring(1) };
    switch (p)
    {
        case FByteProperty b: t["enum"] = Name(b.Enum); break;
        case FEnumProperty e: t["under"] = TypeOf(e.UnderlyingProp); t["enum"] = Name(e.Enum); break;
        case FStructProperty s: t["struct"] = Name(s.Struct); break;
        case FArrayProperty a: t["inner"] = TypeOf(a.Inner); break;
        case FSetProperty s: t["inner"] = TypeOf(s.ElementProp); break;
        case FOptionalProperty o: t["inner"] = TypeOf(o.ValueProperty); break;
        case FMapProperty m: t["key"] = TypeOf(m.KeyProp); t["value"] = TypeOf(m.ValueProp); break;
    }
    return t;
}

Parallel.ForEach(files, new ParallelOptions { MaxDegreeOfParallelism = threads }, file =>
{
    int n = Interlocked.Increment(ref done);
    if (n % 10000 == 0) Console.WriteLine($"  {n} packages, {lines.Count} types ({clock.Elapsed.TotalSeconds:F0} s)");
    IoPackage pkg;
    try
    {
        pkg = provider.LoadPackage(file) as IoPackage;
        if (pkg == null) return;
    }
    catch (Exception ex)
    {
        Interlocked.Increment(ref packageErrors);
        errors.Enqueue($"package {file.Path}: {ex.GetType().Name}: {ex.Message}");
        return;
    }
    for (int i = 0; i < pkg.ExportMap.Length; i++)
    {
        string cls;
        try
        {
            cls = pkg.ResolveObjectIndex(pkg.ExportMap[i].ClassIndex)?.Name.Text ?? "?";
        }
        catch (Exception)
        {
            cls = "?";
        }
        if (!Wanted(cls)) continue;
        classCounts.AddOrUpdate(cls, 1, (_, c) => c + 1);
        string exportName = pkg.CreateFNameFromMappedName(pkg.ExportMap[i].ObjectName).Text;
        try
        {
            var obj = pkg.ExportsLazy[i].Value;
            var row = new Dictionary<string, object>
            {
                ["pkg"] = pkg.Name, ["name"] = exportName, ["cls"] = cls,
                ["objflags"] = (long)pkg.ExportMap[i].ObjectFlags,
            };
            if (obj is UEnum en)
            {
                row["kind"] = 3;
                row["entries"] = en.Names.Select(x => new object[] { x.Item2, x.Item1.Text }).ToArray();
            }
            else if (obj is UStruct st && obj is not UFunction)
            {
                row["kind"] = obj is UClass ? 2 : 1;
                row["flags"] = obj switch
                {
                    UClass c => (long)(uint)c.ClassFlags,
                    UScriptStruct s => (long)(uint)s.StructFlags,
                    UUserDefinedStruct u => (long)u.StructFlags,
                    _ => 0L,
                };
                row["super"] = Name(st.SuperStruct);
                row["props"] = (st.ChildProperties ?? []).Select(f => f is FProperty p
                    ? new Dictionary<string, object>
                    {
                        ["n"] = p.Name.Text, ["d"] = p.ArrayDim, ["f"] = (ulong)p.PropertyFlags, ["t"] = TypeOf(p),
                    }
                    : new Dictionary<string, object> { ["n"] = f.Name.Text, ["t"] = new Dictionary<string, object> { ["k"] = "?field " + f.GetType().Name } }).ToArray();
            }
            else
            {
                continue;
            }
            lines.Enqueue(JsonSerializer.Serialize(row));
        }
        catch (Exception ex)
        {
            Interlocked.Increment(ref exportErrors);
            errors.Enqueue($"export {pkg.Name}.{exportName} ({cls}): {ex.GetType().Name}: {ex.Message}");
        }
    }
});

File.WriteAllLines(outPath, lines);
File.WriteAllLines(outPath + ".errors.txt", errors);
Console.WriteLine($"done: {done} packages, {lines.Count} types, {packageErrors} package errors, {exportErrors} export errors, {clock.Elapsed.TotalSeconds:F0} s");
foreach (var kv in classCounts.OrderByDescending(x => x.Value).Take(40))
    Console.WriteLine($"  {kv.Value,7}  {kv.Key}");
return 0;

// Counts CUE4Parse's warnings during the read test and keeps the first 200 as examples.
class CountingSink : Serilog.Core.ILogEventSink
{
    public int Count;
    public readonly ConcurrentQueue<string> Examples = new();

    public void Emit(Serilog.Events.LogEvent e)
    {
        if (Interlocked.Increment(ref Count) <= 200) Examples.Enqueue("warning: " + e.RenderMessage());
    }
}
