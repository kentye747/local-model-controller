import Foundation

struct SupportedLanguage:Identifiable {let id,name:String}
enum UILanguage {
    static let available:[SupportedLanguage] = {
        guard let url=Bundle.main.resourceURL?.appendingPathComponent("locales/languages.json"),
              let data=try? Data(contentsOf:url),let rows=try? JSONDecoder().decode([[String:String]].self,from:data) else{return []}
        return rows.compactMap {row in guard let id=row["id"],let name=row["name"] else{return nil};return SupportedLanguage(id:id,name:name)}
    }()
    static var resolved:String {
        let chosen=ProcessInfo.processInfo.environment["LMC_LANGUAGE"] ?? UserDefaults.standard.string(forKey:"controllerLanguage") ?? "system"
        if chosen != "system" {return available.contains(where:{$0.id==chosen}) ? chosen : "en"}
        for preferred in Locale.preferredLanguages {
            if let match=available.first(where:{preferred.hasPrefix($0.id) || preferred.split(separator:"-").first == $0.id.split(separator:"-").first}) {return match.id}
        }
        return "en"
    }
    static let catalogs:[String:[String:String]] = {
        var result:[String:[String:String]]=[:]
        for language in available {
            if let url=Bundle.main.resourceURL?.appendingPathComponent("locales/\(language.id).json"),
               let data=try? Data(contentsOf:url),let map=try? JSONDecoder().decode([String:String].self,from:data) {result[language.id]=map}
        }
        return result
    }()
    static var catalog:[String:String] {catalogs["en"] ?? [:]}
    static let patterns:[String:[(NSRegularExpression,String)]] = catalogs.mapValues {map in
        map.keys.filter{$0.contains("%@")}.sorted{$0.count>$1.count}.compactMap {key in
            let pieces=key.components(separatedBy:"%@").map{NSRegularExpression.escapedPattern(for:$0)}
            guard let regex=try? NSRegularExpression(pattern:"^"+pieces.joined(separator:"(.*?)")+"$",options:[.dotMatchesLineSeparators]) else{return nil}
            return (regex,map[key]!)
        }
    }
    static let fragments:[String:[String]] = catalogs.mapValues {$0.keys.filter{!$0.contains("%@") && $0.count>=2}.sorted{$0.count>$1.count}}
}

/// Translate UI-owned strings only. Conversation titles, IDs, paths and model names bypass this function.
func L(_ source:String,language:String?=nil)->String {
    let locale=language ?? UILanguage.resolved
    if locale=="zh-Hans" {return source}
    let map=UILanguage.catalogs[locale] ?? UILanguage.catalog
    if let exact=map[source] {return exact}
    let range=NSRange(source.startIndex...,in:source)
    for (regex,target) in UILanguage.patterns[locale] ?? [] {
        guard let match=regex.firstMatch(in:source,range:range) else{continue}
        let values=(1..<match.numberOfRanges).map {index -> String in
            guard let range=Range(match.range(at:index),in:source) else{return ""};return String(source[range])
        }
        let parts=target.components(separatedBy:"%@")
        guard parts.count==values.count+1 else{continue}
        return zip(parts.dropLast(),values).map{$0+$1}.joined()+parts.last!
    }
    var result=source
    for key in UILanguage.fragments[locale] ?? [] where result.contains(key) {result=result.replacingOccurrences(of:key,with:map[key] ?? key)}
    if result==source && locale != "en" {return L(source,language:"en")}
    return result
}
