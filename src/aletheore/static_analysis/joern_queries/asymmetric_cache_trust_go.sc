// Real CFG-based detector for the asymmetric-cache-trust bug class found
// in the Martian Code Review Bench gold set (grafana/grafana#103633):
// two cache-guard reads in the same function where one returns
// unconditionally on a hit and another only returns conditionally
// (falling through to re-derive via a fresh lookup on some paths). No LLM
// tested that night caught it, and no off-the-shelf scanner's rule
// category covers cross-function/cross-guard trust asymmetry - see
// docs/audits/deterministic_scanner_evaluation.md.
//
// This is the CFG-based sibling of _asymmetric_cache_trust_findings_go in
// github-app/scan_worker/semantic_checks.py (a cheap text-proximity
// heuristic covering the same bug class) - Joern's real graph traversal
// classifies each guard's branch precisely (does every CFG path out of it
// reach a return?) rather than approximating from nearby lines, at the
// real cost of a CPG build + JVM startup. Validated live against the real
// target (grafana-103633's Service.Check) and zero false positives across
// three other real Go repos in the same corpus (grafana-76186, -79265,
// -80329) - see docs/audits/deterministic_scanner_evaluation.md and
// deterministic_scanner_integration_scope.md.
import io.shiftleft.codepropertygraph.generated.nodes._

def isCacheReadCall(c: Call): Boolean = {
  val name = c.name
  val code = c.code
  val nameLooksLikeRead = name.matches("(?i).*(Get|Fetch|Lookup)$") || name.toLowerCase.contains("cached")
  val codeLooksLikeCache = code.toLowerCase.contains("cache")
  nameLooksLikeRead && codeLooksLikeCache
}

// "exists", not "forall": a branching condition node inside the guard's
// branch has MIXED CFG successors (one staying inside the branch, one
// escaping past it) - forall would wrongly treat that node as "safe"
// because not ALL its successors escape. Real bug found and fixed live
// building this query the first time.
def blockEscapesWithoutReturning(block: AstNode): Boolean = {
  val insideIds: Set[Long] = block.ast.id.toSet
  block.ast.exists { n =>
    n.out("CFG").exists { succ =>
      succ.isInstanceOf[CfgNode] &&
      !insideIds.contains(succ.id) &&
      !succ.isInstanceOf[MethodReturn]
    }
  }
}

// Go's `if stmt; cond {}` desugars in the CPG to the guard call and its
// guarding IF as CFG siblings, not nested - confirmed live, not assumed -
// so the guard is found by walking forward along CFG edges from the call
// until reaching a node whose AST parent is the guarding IF.
def guardIfFor(call: Call): Option[ControlStructure] = {
  Iterator.single(call.asInstanceOf[CfgNode])
    .repeat(_.out("CFG").collectAll[CfgNode])(_.maxDepth(10).emit)
    .flatMap { n =>
      n.astParent match {
        case cs: ControlStructure if cs.controlStructureType == "IF" => Some(cs)
        case _ => None
      }
    }
    .toList.headOption
}

case class GuardResult(methodFullName: String, filename: String, guardName: String, unconditional: Boolean, line: Int)

def jsonEscape(s: String): String =
  s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")

@main def main(cpgPath: String, outputPath: String): Unit = {
  importCpg(cpgPath)

  val results = cpg.call.filter(isCacheReadCall).flatMap { call =>
    guardIfFor(call).toList.flatMap { ifNode =>
      ifNode.astChildren.collectAll[Block].headOption.map { block =>
        val escapes = blockEscapesWithoutReturning(block)
        GuardResult(call.method.fullName, call.file.name.headOption.getOrElse(""), call.name, unconditional = !escapes, call.lineNumber.getOrElse(0))
      }
    }
  }.l

  val findings = scala.collection.mutable.ListBuffer[String]()
  results.groupBy(_.methodFullName).foreach { case (_, guards) =>
    val byName = guards.groupBy(_.guardName).view.mapValues(_.map(_.unconditional)).toMap
    if (byName.size >= 2) {
      val unconditionalNames = byName.filter(_._2.exists(identity)).keys.toSet
      val conditionalNames = byName.filter(g => !g._2.exists(identity)).keys.toSet
      if (unconditionalNames.nonEmpty && conditionalNames.nonEmpty) {
        // One finding per unconditional guard (the immediately-trusting
        // one - the more actionable line to point a reviewer at), naming
        // the conditional guard(s) it's asymmetric against in the message.
        guards.filter(g => unconditionalNames.contains(g.guardName) && g.unconditional).foreach { g =>
          val message = s"'${g.guardName}' returns immediately on a cache hit here, but this function also has a " +
            s"differently-behaving cache guard (${conditionalNames.mkString(", ")}) that only returns conditionally, " +
            "re-deriving via a fresh lookup on some paths. A stale/revoked cache entry on this immediate-return path " +
            "can outlive what the conditional guard would have caught."
          findings += "{" +
            "\"tool\":\"joern\"," +
            "\"rule_id\":\"asymmetric-cache-trust-go\"," +
            "\"severity\":\"critical\"," +
            "\"type\":\"vulnerability\"," +
            s"""\"path\":\"${jsonEscape(g.filename)}\",""" +
            s"""\"line\":${g.line},""" +
            s"""\"message\":\"${jsonEscape(message)}\"""" +
            "}"
        }
      }
    }
  }

  val json = "[" + findings.mkString(",") + "]"
  better.files.File(outputPath).writeText(json)
}
