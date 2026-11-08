"""Java refine: shapes found by analysing the OWASP Benchmark's remaining false positives. Each test is a pared-down version
of a pattern that used to leave a safe test flagged, plus the vulnerable twin that must stay flagged."""

from aletheore.flow.validate import validate_finding


def verdict(tmp_path, body, extra="", needle=None, cwes=frozenset({89}), whole_statement=False):
    src = ("import javax.servlet.http.*;\npublic class T extends HttpServlet {\n" + extra +
           "\n  protected void doPost(HttpServletRequest request, HttpServletResponse response) throws Exception {\n" + body + "\n  }\n}\n")
    (tmp_path / "T.java").write_text(src)
    lines = src.splitlines()
    first = next(i for i, l in enumerate(lines, 1) if needle in l)
    if whole_statement:
        last = next(i for i in range(first, len(lines) + 1) if lines[i - 1].rstrip().endswith(";") and i > first)
        return validate_finding(tmp_path, "T.java", (first, lines[first - 1].index(needle) + 1), (last, len(lines[last - 1]) + 1), set(cwes))
    col = lines[first - 1].index(needle) + 1
    return validate_finding(tmp_path, "T.java", (first, col), (first, len(lines[first - 1]) + 1), set(cwes))


def test_wide_taint_range_judges_the_sink_not_the_neighbours(tmp_path):
    # Semgrep reports source-to-sink spans; helper calls inside (no arguments, unknown receivers) are not sinks.
    body = ('    String bar = "const";\n    String sql = "select " + bar;\n    java.sql.Statement st = helper();\n'
            '    st.addBatch(sql);\n    int[] n = st.executeBatch();\n    print(sql, n, response);')
    v = verdict(tmp_path, body, "  java.sql.Statement helper() { return null; }\n  void print(String a, int[] b, Object c) {}", needle="java.sql.Statement st", whole_statement=False)
    # the span from the declaration to the end of print(...)
    src = (tmp_path / "T.java").read_text().splitlines()
    first = next(i for i, l in enumerate(src, 1) if "java.sql.Statement st = helper" in l)
    last = next(i for i, l in enumerate(src, 1) if "print(sql, n, response)" in l)
    assert validate_finding(tmp_path, "T.java", (first, 5), (last, len(src[last - 1])), {89}).droppable
    tainted = body.replace('String bar = "const";', 'String bar = request.getParameter("p");')
    verdict(tmp_path, tainted, "  java.sql.Statement helper() { return null; }\n  void print(String a, int[] b, Object c) {}", needle="java.sql.Statement st")
    src = (tmp_path / "T.java").read_text().splitlines()
    first = next(i for i, l in enumerate(src, 1) if "java.sql.Statement st = helper" in l)
    last = next(i for i, l in enumerate(src, 1) if "print(sql, n, response)" in l)
    assert not validate_finding(tmp_path, "T.java", (first, 5), (last, len(src[last - 1])), {89}).droppable


def test_runtime_exec_judges_the_environment_too(tmp_path):
    body = '    String[] env = {request.getParameter("e")};\n    Runtime.getRuntime().exec("ls", env);'
    assert not verdict(tmp_path, body, needle="Runtime.getRuntime().exec", cwes={78}).droppable
    safe = '    String[] env = {"A=b"};\n    Runtime.getRuntime().exec("ls", env);'
    assert verdict(tmp_path, safe, needle="Runtime.getRuntime().exec", cwes={78}).droppable


def test_class_loader_metadata_is_not_request_data(tmp_path):
    body = '    String cmd = helper(this.getClass().getClassLoader());\n    Runtime.getRuntime().exec(cmd);'
    extra = '  String helper(ClassLoader c) { return c.getResource("x.sh").getPath(); }'
    assert verdict(tmp_path, body, extra, needle="Runtime.getRuntime().exec", cwes={78}).droppable


def test_all_caps_field_is_a_constant_not_a_class(tmp_path):
    extra = '  static final String DIR = System.getProperty("user.dir") + java.io.File.separator;'
    body = '    new java.io.File(DIR.replace(\'\\\\\', \'/\') + "x").delete();\n    new java.io.FileInputStream(DIR.replace(\' \', \'_\') + "x");'
    assert verdict(tmp_path, body, extra, needle="new java.io.FileInputStream", cwes={22}).droppable


def test_compiled_xpath_evaluate_takes_the_document(tmp_path):
    body = ('    String e = "/a[@id=\'" + "x" + "\']";\n    javax.xml.xpath.XPath xp = javax.xml.xpath.XPathFactory.newInstance().newXPath();\n'
            '    xp.compile(e).evaluate(null, javax.xml.xpath.XPathConstants.NODESET);')
    assert verdict(tmp_path, body, needle="xp.compile(e)", cwes={643}).droppable
    bad = body.replace('"x"', 'request.getParameter("p")')
    assert not verdict(tmp_path, bad, needle="xp.compile(e)", cwes={643}).droppable


def test_encoder_round_trip_keeps_taint_and_constants(tmp_path):
    body = ('    String bar = new String(org.apache.commons.codec.binary.Base64.decodeBase64(org.apache.commons.codec.binary.Base64.encodeBase64("c".getBytes())));\n'
            '    java.sql.DriverManager.getConnection("x").createStatement().executeQuery("select \'" + bar + "\'");')
    assert verdict(tmp_path, body, needle="java.sql.DriverManager", cwes={89}).droppable
    bad = body.replace('"c".getBytes()', 'request.getParameter("p").getBytes()')
    assert not verdict(tmp_path, bad, needle="java.sql.DriverManager", cwes={89}).droppable


def test_jdbc_template_query_sql_is_the_judged_argument(tmp_path):
    extra = "  static org.springframework.jdbc.core.JdbcTemplate T;"
    body = '    String sql = "select 1";\n    java.util.List<String> r = T.query(sql, new org.springframework.jdbc.core.RowMapper<String>() { public String mapRow(java.sql.ResultSet rs, int i) { return ""; } });'
    assert verdict(tmp_path, body, extra, needle="java.util.List<String> r").droppable
