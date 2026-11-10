"""MyBatis `${...}` placeholders: a mapper method whose SQL pastes a parameter in is a SQL sink at that parameter."""
from pathlib import Path

from aletheore.flow.discover import discover_repo

MAPPER = """package app;
import org.apache.ibatis.annotations.*;
import java.util.List;
@Mapper
public interface UserMapper {
    @Select("select * from users where name = #{name}")
    List<String> safeByName(@Param("name") String name);
    @Select("select * from users where name = '${name}'")
    List<String> vulnByName(@Param("name") String name);
    List<String> vulnOrder(@Param("col") String col, @Param("limit") int limit);
    List<String> xmlSafe(String name);
}
"""
XML = """<?xml version="1.0" encoding="UTF-8"?>
<mapper namespace="app.UserMapper">
  <!-- <select id="xmlSafe">select * from users where name = '${name}'</select> -->
  <select id="vulnOrder" resultType="String">select name from users order by ${col} limit #{limit}</select>
  <select id="xmlSafe" resultType="String">select name from users where name = #{name}</select>
</mapper>
"""
CTRL = """package app;
import org.springframework.web.bind.annotation.*;
@RestController
public class C {
    private UserMapper userMapper;
    @GetMapping("/a") public Object a(@RequestParam String q) { return userMapper.safeByName(q); }
    @GetMapping("/b") public Object b(@RequestParam String q) { return userMapper.vulnByName(q); }
    @GetMapping("/c") public Object c(@RequestParam String q) { return userMapper.vulnOrder(q, 10); }
    @GetMapping("/d") public Object d(@RequestParam String q) { return userMapper.xmlSafe(q); }
    @GetMapping("/e") public Object e(@RequestParam String q) { return userMapper.vulnOrder("name", 10); }
}
"""


def test_mybatis_dollar_placeholders(tmp_path: Path):
    (tmp_path / "src/main/java/app").mkdir(parents=True)
    (tmp_path / "src/main/resources/mapper").mkdir(parents=True)
    (tmp_path / "src/main/java/app/UserMapper.java").write_text(MAPPER)
    (tmp_path / "src/main/java/app/C.java").write_text(CTRL)
    (tmp_path / "src/main/resources/mapper/UserMapper.xml").write_text(XML)
    lines = sorted(f["line"] for f in discover_repo(tmp_path) if f["rule_id"].endswith("sql-injection"))
    assert lines == [7, 8]  # vulnByName (annotation ${}) and vulnOrder (XML ${col}); #{} and constant arguments are quiet
