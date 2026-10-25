# Java Client and Utility Library Installation

## Install Client Library

Determine the latest version of `com.google.api-ads:data-manager` by running:
```shell
curl -s "https://repo1.maven.org/maven2/com/google/api-ads/data-manager/maven-metadata.xml" \
  | sed -n 's/.*<release>\(.*\)<\/release>.*/\1/p'
```

If using Maven, add the following dependency to your `pom.xml` file.
Replace `{latest_version}` with the version retrieved above:

```xml
<dependency>
  <groupId>com.google.api-ads</groupId>
  <artifactId>data-manager</artifactId>
  <version>{latest_version}</version>
</dependency>
```

If using Gradle, add this to your dependencies.
Replace `{latest_version}` with the version retrieved above:

```groovy
implementation 'com.google.api-ads:data-manager:{latest_version}'
```

## Install Utility Library

Determine the latest version of `com.google.api-ads:data-manager-util` by running:
```shell
curl -s "https://repo1.maven.org/maven2/com/google/api-ads/data-manager-util/maven-metadata.xml" \
  | sed -n 's/.*<release>\(.*\)<\/release>.*/\1/p'
```

If using Maven, add the following dependency to your `pom.xml` file.
Replace `{latest_version}` with the version retrieved above:

```xml
<dependency>
  <groupId>com.google.api-ads</groupId>
  <artifactId>data-manager-util</artifactId>
  <version>{latest_version}</version>
</dependency>
```

If using Gradle, add this to your dependencies.
Replace `{latest_version}` with the version retrieved above:

```groovy
implementation 'com.google.api-ads:data-manager-util:{latest_version}'
```
