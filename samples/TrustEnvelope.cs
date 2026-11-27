// The trust envelope from the MCP server (C#, .NET).
//
// Lightly edited from the production code. Every tool result the server returns
// is a value, its origin and its trust class, so an AI assistant can tell
// "what the document says" apart from "what you should do".
//
// Pattern: make the wrong thing hard to write. Text from a document someone else
// authored is wrapped in UntrustedText, whose ToString() hides the content, so it
// can't be pasted into a summary by accident. The summary line, which the
// assistant replays on every later turn, is checked to contain no untrusted text.

using System.Text.Json.Serialization;

namespace EvidenceMcp.Envelope;

/// <summary>How much a piece of content in a tool result may be trusted.</summary>
public enum TrustClass
{
    /// <summary>Written by our own code: labels, column names, counts.</summary>
    System,

    /// <summary>Computed by the platform from stored data: amounts, dates, totals.</summary>
    Derived,

    /// <summary>
    /// Text from a file the user stored but did not write. A bank wrote the
    /// statement, a merchant wrote the receipt. Anyone who can get a document
    /// uploaded can put text here.
    /// </summary>
    UntrustedDocument,
}

/// <summary>A string that came from outside and must never be treated as an instruction.</summary>
public sealed class UntrustedText
{
    private readonly string _value;

    public UntrustedText(string value) => _value = value ?? string.Empty;

    public int Length => _value.Length;

    /// <summary>
    /// The real content. Its only legitimate destination is a labelled content block
    /// being serialised. The long name is deliberate: easy to spot in review, easy to grep.
    /// </summary>
    public string RevealForSerialization() => _value;

    /// <summary>Deliberately lossy, so accidental interpolation is harmless and visible.</summary>
    public override string ToString() => $"<untrusted:{_value.Length} chars>";
}

/// <summary>
/// One piece of content, carrying its own trust class. Untrusted content gets its
/// own block rather than being fenced inside a larger string: delimiters can be
/// guessed, and structural separation has no delimiter to guess.
/// </summary>
public sealed class ContentBlock
{
    [JsonPropertyName("trust")] public string Trust { get; }
    [JsonPropertyName("kind")] public string Kind { get; }
    [JsonPropertyName("data")] public object? Data { get; }

    /// <summary>Instruction-like passages found in untrusted text: flagged, never stripped.</summary>
    [JsonPropertyName("flags")] public IReadOnlyList<string>? Flags { get; }

    private ContentBlock(string trust, string kind, object? data, IReadOnlyList<string>? flags = null)
    {
        Trust = trust;
        Kind = kind;
        Data = data;
        Flags = flags;
    }

    public static ContentBlock System(string kind, object? data) => new("system", kind, data);

    public static ContentBlock Derived(string kind, object? data) => new("derived", kind, data);

    /// <summary>
    /// The only way to build an untrusted block. It takes UntrustedText, not a string,
    /// so a call site can't pass server-composed text by accident.
    /// </summary>
    public static ContentBlock Untrusted(UntrustedText text, IReadOnlyList<string> flags) =>
        new("untrusted_document", "text", text.RevealForSerialization(), flags);

    [JsonIgnore] public bool IsUntrusted => Trust == "untrusted_document";
}

/// <summary>A tool result is not a value. It is a value, its origin, and its trust class.</summary>
public sealed class ToolEnvelope
{
    [JsonPropertyName("outcome")] public string Outcome { get; init; } = "found";

    /// <summary>A handle the assistant can pass to explain() to trace a figure to its source.</summary>
    [JsonPropertyName("claim_id")] public string? ClaimId { get; init; }

    /// <summary>
    /// One line, composed only from system and derived content. The assistant replays it
    /// on every later turn, which makes it the most valuable place to smuggle an instruction.
    /// </summary>
    [JsonPropertyName("summary")] public string Summary { get; init; } = string.Empty;

    [JsonPropertyName("content")] public IReadOnlyList<ContentBlock> Content { get; init; } = Array.Empty<ContentBlock>();

    /// <summary>
    /// Returns the untrusted fragment that leaked into the summary, if any, so a failing
    /// test names what leaked rather than just that something did.
    /// </summary>
    public string? FindUntrustedLeakInSummary()
    {
        if (string.IsNullOrEmpty(Summary))
        {
            return null;
        }

        foreach (var block in Content.Where(b => b.IsUntrusted))
        {
            if (block.Data is not string raw || raw.Length < MinLeakFragment)
            {
                continue;
            }

            // A leak is rarely the whole payload; a truncated quote is still a leak.
            for (var i = 0; i + MinLeakFragment <= raw.Length; i++)
            {
                var fragment = raw.Substring(i, MinLeakFragment);
                if (Summary.Contains(fragment, StringComparison.Ordinal))
                {
                    return fragment;
                }
            }
        }

        return null;
    }

    /// <summary>Short enough to catch a truncated quote; long enough to ignore a shared common word.</summary>
    private const int MinLeakFragment = 24;
}
