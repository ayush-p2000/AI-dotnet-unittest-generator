using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Xml.Linq;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace RoslynScanner
{
    public class Manifest
    {
        [JsonPropertyName("root")]
        public string Root { get; set; } = string.Empty;

        [JsonPropertyName("projects")]
        public List<ProjectInfo> Projects { get; set; } = new();

        [JsonPropertyName("symbol_table")]
        public Dictionary<string, object> SymbolTable { get; set; } = new();
    }

    public class ProjectInfo
    {
        [JsonPropertyName("csproj")]
        public string Csproj { get; set; } = string.Empty;

        [JsonPropertyName("project_name")]
        public string ProjectName { get; set; } = string.Empty;

        [JsonPropertyName("target_framework")]
        public string TargetFramework { get; set; } = "net8.0";

        [JsonPropertyName("packages")]
        public List<PackageInfo> Packages { get; set; } = new();

        [JsonPropertyName("project_references")]
        public List<string> ProjectReferences { get; set; } = new();

        [JsonPropertyName("files")]
        public List<FileInfoModel> Files { get; set; } = new();
    }

    public class PackageInfo
    {
        [JsonPropertyName("name")]
        public string Name { get; set; } = string.Empty;

        [JsonPropertyName("version")]
        public string Version { get; set; } = string.Empty;
    }

    public class FileInfoModel
    {
        [JsonPropertyName("file_name")]
        public string FileName { get; set; } = string.Empty;

        [JsonPropertyName("path")]
        public string Path { get; set; } = string.Empty;

        [JsonPropertyName("namespace")]
        public string? Namespace { get; set; }

        [JsonPropertyName("usings")]
        public List<string> Usings { get; set; } = new();

        [JsonPropertyName("types")]
        public List<TypeInfoModel> Types { get; set; } = new();
    }

    public class TypeInfoModel
    {
        [JsonPropertyName("modifiers")]
        public string Modifiers { get; set; } = string.Empty;

        [JsonPropertyName("kind")]
        public string Kind { get; set; } = string.Empty;

        [JsonPropertyName("name")]
        public string Name { get; set; } = string.Empty;

        [JsonPropertyName("generics")]
        public string? Generics { get; set; }

        [JsonPropertyName("bases")]
        public List<string> Bases { get; set; } = new();

        [JsonPropertyName("constructors")]
        public List<ConstructorInfoModel> Constructors { get; set; } = new();

        [JsonPropertyName("properties")]
        public List<PropertyInfoModel> Properties { get; set; } = new();

        [JsonPropertyName("fields")]
        public List<FieldInfoModel> Fields { get; set; } = new();

        [JsonPropertyName("methods")]
        public List<MethodInfoModel> Methods { get; set; } = new();

        [JsonPropertyName("enum_members")]
        public List<EnumMemberModel> EnumMembers { get; set; } = new();
    }

    public class ConstructorInfoModel
    {
        [JsonPropertyName("modifiers")]
        public string Modifiers { get; set; } = "public";

        [JsonPropertyName("params")]
        public string Params { get; set; } = string.Empty;

        [JsonPropertyName("style")]
        public string Style { get; set; } = "traditional";
    }

    public class PropertyInfoModel
    {
        [JsonPropertyName("modifiers")]
        public string Modifiers { get; set; } = string.Empty;

        [JsonPropertyName("type")]
        public string Type { get; set; } = string.Empty;

        [JsonPropertyName("name")]
        public string Name { get; set; } = string.Empty;

        [JsonPropertyName("has_getter")]
        public bool HasGetter { get; set; }

        [JsonPropertyName("has_setter")]
        public bool HasSetter { get; set; }

        [JsonPropertyName("is_expression_bodied")]
        public bool IsExpressionBodied { get; set; }

        [JsonPropertyName("initial_value")]
        public string? InitialValue { get; set; }
    }

    public class FieldInfoModel
    {
        [JsonPropertyName("modifiers")]
        public string Modifiers { get; set; } = string.Empty;

        [JsonPropertyName("type")]
        public string Type { get; set; } = string.Empty;

        [JsonPropertyName("name")]
        public string Name { get; set; } = string.Empty;

        [JsonPropertyName("is_const")]
        public bool IsConst { get; set; }

        [JsonPropertyName("is_static")]
        public bool IsStatic { get; set; }

        [JsonPropertyName("is_readonly")]
        public bool IsReadonly { get; set; }

        [JsonPropertyName("value")]
        public string? Value { get; set; }
    }

    public class MethodInfoModel
    {
        [JsonPropertyName("modifiers")]
        public string Modifiers { get; set; } = string.Empty;

        [JsonPropertyName("return_type")]
        public string ReturnType { get; set; } = string.Empty;

        [JsonPropertyName("name")]
        public string Name { get; set; } = string.Empty;

        [JsonPropertyName("generics")]
        public string? Generics { get; set; }

        [JsonPropertyName("params")]
        public string Params { get; set; } = string.Empty;
    }

    public class EnumMemberModel
    {
        [JsonPropertyName("name")]
        public string Name { get; set; } = string.Empty;

        [JsonPropertyName("value")]
        public string? Value { get; set; }
    }

    public static class Program
    {
        private static readonly HashSet<string> ExcludeDirs = new(StringComparer.OrdinalIgnoreCase)
        {
            "bin", "obj", ".venv", ".git", ".github", ".vs", "node_modules", "packages",
            "tests", "test", "samples", "sample", "benchmarks", "benchmark", "demos", "demo", "docs"
        };

        private static readonly string[] ExcludeSuffixes = new[]
        {
            ".Designer.cs", ".g.cs", ".g.i.cs", "AssemblyInfo.cs"
        };

        public static int Main(string[] args)
        {
            if (args.Length == 0)
            {
                Console.Error.WriteLine("Usage: RoslynScanner <path-to-target> [--output <path>]");
                return 1;
            }

            string targetPath = Path.GetFullPath(args[0]);
            string? outputPath = null;

            for (int i = 1; i < args.Length; i++)
            {
                if (args[i] == "--output" && i + 1 < args.Length)
                {
                    outputPath = Path.GetFullPath(args[i + 1]);
                    i++;
                }
            }

            try
            {
                var manifest = Scan(targetPath);
                var options = new JsonSerializerOptions
                {
                    WriteIndented = true,
                    Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
                    DefaultIgnoreCondition = JsonIgnoreCondition.Never
                };

                string json = JsonSerializer.Serialize(manifest, options);

                if (!string.IsNullOrEmpty(outputPath))
                {
                    var dir = Path.GetDirectoryName(outputPath);
                    if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                    {
                        Directory.CreateDirectory(dir);
                    }
                    File.WriteAllText(outputPath, json);
                    Console.WriteLine($"[RoslynScanner] Written manifest to {outputPath}");
                }
                else
                {
                    Console.WriteLine(json);
                }

                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"[RoslynScanner ERROR] {ex.Message}\n{ex.StackTrace}");
                return 1;
            }
        }

        public static Manifest Scan(string targetPath)
        {
            var manifest = new Manifest
            {
                Root = targetPath
            };

            var csprojFiles = FindCsprojFiles(targetPath);

            foreach (var csproj in csprojFiles)
            {
                if (IsTestProject(csproj))
                {
                    continue;
                }

                var (targetFramework, packages, projectReferences) = ParseCsprojMetadata(csproj);
                var csFiles = FindCsFilesForProject(csproj);

                var proj = new ProjectInfo
                {
                    Csproj = csproj,
                    ProjectName = Path.GetFileNameWithoutExtension(csproj),
                    TargetFramework = targetFramework,
                    Packages = packages,
                    ProjectReferences = projectReferences
                };

                foreach (var csFile in csFiles)
                {
                    try
                    {
                        var fileInfo = ParseCsFile(csFile);
                        proj.Files.Add(fileInfo);
                    }
                    catch (Exception ex)
                    {
                        Console.Error.WriteLine($"Warning: Failed to parse {csFile}: {ex.Message}");
                    }
                }

                manifest.Projects.Add(proj);
            }

            return manifest;
        }

        private static List<string> FindCsprojFiles(string root)
        {
            var results = new List<string>();
            if (File.Exists(root) && root.EndsWith(".csproj", StringComparison.OrdinalIgnoreCase))
            {
                results.Add(root);
                return results;
            }

            if (!Directory.Exists(root))
            {
                return results;
            }

            void Recurse(string dir)
            {
                var dirName = Path.GetFileName(dir);
                if (ExcludeDirs.Contains(dirName)) return;

                try
                {
                    foreach (var file in Directory.GetFiles(dir, "*.csproj"))
                    {
                        results.Add(file);
                    }

                    foreach (var sub in Directory.GetDirectories(dir))
                    {
                        Recurse(sub);
                    }
                }
                catch
                {
                    // Ignore inaccessible folders
                }
            }

            Recurse(root);
            return results;
        }

        private static List<string> FindCsFilesForProject(string csprojPath)
        {
            var results = new List<string>();
            var projectDir = Path.GetDirectoryName(csprojPath);
            if (string.IsNullOrEmpty(projectDir) || !Directory.Exists(projectDir))
                return results;

            void Recurse(string dir)
            {
                var dirName = Path.GetFileName(dir);
                if (ExcludeDirs.Contains(dirName)) return;

                try
                {
                    foreach (var file in Directory.GetFiles(dir, "*.cs"))
                    {
                        var fileName = Path.GetFileName(file);
                        if (ExcludeSuffixes.Any(suffix => fileName.EndsWith(suffix, StringComparison.OrdinalIgnoreCase)))
                        {
                            continue;
                        }
                        results.Add(file);
                    }

                    foreach (var sub in Directory.GetDirectories(dir))
                    {
                        Recurse(sub);
                    }
                }
                catch
                {
                    // Ignore inaccessible folders
                }
            }

            Recurse(projectDir);
            return results;
        }

        private static bool IsTestProject(string csprojPath)
        {
            try
            {
                string text = File.ReadAllText(csprojPath).ToLowerInvariant();
                if (text.Contains("microsoft.net.test.sdk")) return true;
                if (text.Replace(" ", "").Contains("<istestproject>true</istestproject>")) return true;
                if (text.Contains("xunit") || text.Contains("nunit") || text.Contains("mstest")) return true;
            }
            catch { }

            string name = Path.GetFileNameWithoutExtension(csprojPath).ToLowerInvariant();
            return name.EndsWith("tests") || name.EndsWith("test") || name.Contains(".tests.") || name.Contains(".test.");
        }

        private static (string TargetFramework, List<PackageInfo> Packages, List<string> ProjectReferences) ParseCsprojMetadata(string csprojPath)
        {
            string targetFramework = "net8.0";
            var packages = new List<PackageInfo>();
            var projectReferences = new List<string>();

            try
            {
                var doc = XDocument.Load(csprojPath);
                var tfElem = doc.Descendants("TargetFramework").FirstOrDefault();
                if (tfElem != null && !string.IsNullOrWhiteSpace(tfElem.Value))
                {
                    targetFramework = tfElem.Value.Trim();
                }
                else
                {
                    var tfsElem = doc.Descendants("TargetFrameworks").FirstOrDefault();
                    if (tfsElem != null && !string.IsNullOrWhiteSpace(tfsElem.Value))
                    {
                        targetFramework = tfsElem.Value.Trim();
                    }
                }

                foreach (var pkg in doc.Descendants("PackageReference"))
                {
                    var inc = pkg.Attribute("Include")?.Value ?? pkg.Attribute("Update")?.Value;
                    if (!string.IsNullOrEmpty(inc))
                    {
                        var ver = pkg.Attribute("Version")?.Value ?? pkg.Element("Version")?.Value ?? "";
                        packages.Add(new PackageInfo { Name = inc, Version = ver });
                    }
                }

                foreach (var pr in doc.Descendants("ProjectReference"))
                {
                    var inc = pr.Attribute("Include")?.Value;
                    if (!string.IsNullOrEmpty(inc))
                    {
                        projectReferences.Add(inc);
                    }
                }
            }
            catch { }

            return (targetFramework, packages, projectReferences);
        }

        private static FileInfoModel ParseCsFile(string filePath)
        {
            string text = File.ReadAllText(filePath);
            var tree = CSharpSyntaxTree.ParseText(text);
            var root = tree.GetCompilationUnitRoot();

            // Namespace
            string? ns = null;
            var fileScopedNs = root.DescendantNodes().OfType<FileScopedNamespaceDeclarationSyntax>().FirstOrDefault();
            if (fileScopedNs != null)
            {
                ns = fileScopedNs.Name.ToString();
            }
            else
            {
                var standardNs = root.DescendantNodes().OfType<NamespaceDeclarationSyntax>().FirstOrDefault();
                if (standardNs != null)
                {
                    ns = standardNs.Name.ToString();
                }
            }

            // Usings
            var usings = root.Usings.Select(u => u.Name?.ToString() ?? "").Where(u => !string.IsNullOrEmpty(u)).Distinct().ToList();

            var fileModel = new FileInfoModel
            {
                FileName = Path.GetFileName(filePath),
                Path = filePath,
                Namespace = ns,
                Usings = usings
            };

            // Types: class, interface, struct, record, enum
            var typeDeclarations = root.DescendantNodes().OfType<BaseTypeDeclarationSyntax>();
            foreach (var td in typeDeclarations)
            {
                var typeModel = ExtractTypeInfo(td);
                fileModel.Types.Add(typeModel);
            }

            return fileModel;
        }

        private static TypeInfoModel ExtractTypeInfo(BaseTypeDeclarationSyntax td)
        {
            string modifiers = td.Modifiers.ToString();
            string kind = td switch
            {
                ClassDeclarationSyntax => "class",
                InterfaceDeclarationSyntax => "interface",
                StructDeclarationSyntax => "struct",
                RecordDeclarationSyntax r when r.Kind() == SyntaxKind.RecordStructDeclaration => "record struct",
                RecordDeclarationSyntax => "record",
                EnumDeclarationSyntax => "enum",
                _ => "class"
            };

            string name = td.Identifier.Text;
            string? generics = (td as TypeDeclarationSyntax)?.TypeParameterList?.ToString();

            var bases = new List<string>();
            if (td.BaseList != null)
            {
                foreach (var bt in td.BaseList.Types)
                {
                    bases.Add(bt.Type.ToString());
                }
            }

            var typeModel = new TypeInfoModel
            {
                Modifiers = modifiers,
                Kind = kind,
                Name = name,
                Generics = generics,
                Bases = bases
            };

            if (td is EnumDeclarationSyntax enumDecl)
            {
                foreach (var member in enumDecl.Members)
                {
                    typeModel.EnumMembers.Add(new EnumMemberModel
                    {
                        Name = member.Identifier.Text,
                        Value = member.EqualsValue?.Value.ToString()
                    });
                }
                return typeModel;
            }

            if (td is not TypeDeclarationSyntax typeDecl)
            {
                return typeModel;
            }

            // Check primary constructor (C# 12 / record)
            if (typeDecl.ParameterList != null)
            {
                string pStr = string.Join(", ", typeDecl.ParameterList.Parameters.Select(p => p.ToString()));
                typeModel.Constructors.Add(new ConstructorInfoModel
                {
                    Modifiers = "public",
                    Params = pStr,
                    Style = "primary"
                });
            }

            // Traditional constructors
            foreach (var ctor in typeDecl.Members.OfType<ConstructorDeclarationSyntax>())
            {
                string pStr = string.Join(", ", ctor.ParameterList.Parameters.Select(p => p.ToString()));
                typeModel.Constructors.Add(new ConstructorInfoModel
                {
                    Modifiers = ctor.Modifiers.ToString(),
                    Params = pStr,
                    Style = "traditional"
                });
            }

            // Properties
            foreach (var prop in typeDecl.Members.OfType<PropertyDeclarationSyntax>())
            {
                bool hasGetter = false;
                bool hasSetter = false;

                if (prop.ExpressionBody != null)
                {
                    hasGetter = true;
                }
                else if (prop.AccessorList != null)
                {
                    hasGetter = prop.AccessorList.Accessors.Any(a => a.IsKind(SyntaxKind.GetAccessorDeclaration));
                    hasSetter = prop.AccessorList.Accessors.Any(a => a.IsKind(SyntaxKind.SetAccessorDeclaration) || a.IsKind(SyntaxKind.InitAccessorDeclaration));
                }

                string? initVal = prop.Initializer?.Value.ToString() ?? prop.ExpressionBody?.Expression.ToString();
                string propMods = prop.Modifiers.ToString();
                if (string.IsNullOrEmpty(propMods) && td is InterfaceDeclarationSyntax)
                {
                    propMods = "public";
                }

                typeModel.Properties.Add(new PropertyInfoModel
                {
                    Modifiers = propMods,
                    Type = prop.Type.ToString(),
                    Name = prop.Identifier.Text,
                    HasGetter = hasGetter,
                    HasSetter = hasSetter,
                    IsExpressionBodied = prop.ExpressionBody != null,
                    InitialValue = initVal
                });
            }

            // Fields
            foreach (var field in typeDecl.Members.OfType<FieldDeclarationSyntax>())
            {
                string fieldMods = field.Modifiers.ToString();
                string fieldType = field.Declaration.Type.ToString();
                bool isConst = field.Modifiers.Any(SyntaxKind.ConstKeyword);
                bool isStatic = field.Modifiers.Any(SyntaxKind.StaticKeyword);
                bool isReadonly = field.Modifiers.Any(SyntaxKind.ReadOnlyKeyword);

                foreach (var v in field.Declaration.Variables)
                {
                    typeModel.Fields.Add(new FieldInfoModel
                    {
                        Modifiers = fieldMods,
                        Type = fieldType,
                        Name = v.Identifier.Text,
                        IsConst = isConst,
                        IsStatic = isStatic,
                        IsReadonly = isReadonly,
                        Value = v.Initializer?.Value.ToString()
                    });
                }
            }

            // Methods
            foreach (var method in typeDecl.Members.OfType<MethodDeclarationSyntax>())
            {
                string pStr = string.Join(", ", method.ParameterList.Parameters.Select(p => p.ToString()));
                string methodMods = method.Modifiers.ToString();
                if (string.IsNullOrEmpty(methodMods) && td is InterfaceDeclarationSyntax)
                {
                    methodMods = "public";
                }

                typeModel.Methods.Add(new MethodInfoModel
                {
                    Modifiers = methodMods,
                    ReturnType = method.ReturnType.ToString(),
                    Name = method.Identifier.Text,
                    Generics = method.TypeParameterList?.ToString(),
                    Params = pStr
                });
            }

            return typeModel;
        }
    }
}
