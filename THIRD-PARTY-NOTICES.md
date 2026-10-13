# 第三方组件声明 (Third-Party Notices)

DocGraph 使用了下列第三方开源组件。本文件说明各组件的版本、许可证类型，并附上
分发时需要随附的许可证全文。

- 组件版本以 `frontend/package.json` 与 `backend/requirements.txt` 为准，
  实际解析版本见 `frontend/pnpm-lock.yaml`、`package-lock.json` 与后端虚拟环境。
- 每个组件的版权声明（copyright notice）保留在依赖包自带的许可文件中：
  前端在 `frontend/node_modules/<组件>/LICENSE`，后端在
  `backend/.venv/Lib/site-packages/<组件>-<版本>.dist-info/` 下的许可文件。
- 升级依赖后请同步更新本文件的版本号。

---

## 一、前端运行时依赖（随构建产物分发）

| 组件 | 版本 | 许可证 |
| --- | --- | --- |
| react | 18.3.1 | MIT |
| react-dom | 18.3.1 | MIT |
| react-router-dom | 7.18.3 | MIT |
| react-pdf | 10.5.0 | MIT |
| antd | 5.29.3 | MIT |
| @ant-design/icons | 5.6.1 | MIT |
| axios | 1.20.0 | MIT |
| dayjs | 1.11.23 | MIT |
| js-yaml | 5.4.1 | MIT |
| d3 | 7.9.0 | ISC |
| mammoth | 1.12.2 | BSD-2-Clause |
| pdfjs-dist | 5.4.296 | Apache-2.0 |

以上均为宽松许可证：允许商用、修改、再分发，义务是保留版权声明与许可证声明。

## 二、后端运行时依赖（服务端安装）

| 组件 | 版本 | 许可证 |
| --- | --- | --- |
| fastapi | 0.115.6 | MIT |
| uvicorn | 0.34.0 | BSD-3-Clause |
| python-multipart | 0.0.20 | Apache-2.0 |
| SQLAlchemy | 2.0.36 | MIT |
| alembic | 1.14.0 | MIT |
| psycopg2-binary | 2.9.10 | LGPL-3.0 + 例外（见第五节） |
| neo4j（Python 驱动） | 5.26.0 | Apache-2.0 |
| openai | 1.59.6 | Apache-2.0 |
| httpx | 0.28.1 | BSD-3-Clause |
| pydantic | 2.10.4 | MIT |
| pydantic-settings | 2.7.0 | MIT |
| python-dotenv | 1.0.1 | BSD-3-Clause |
| PyYAML | 6.0.3 | MIT |
| loguru | 0.7.3 | MIT |
| pdfplumber | 0.11.4 | MIT |
| PyMuPDF | 1.25.1 | AGPL-3.0（见第五节） |
| Pillow | 11.0.0 | MIT-CMU（含捆绑库声明） |
| python-docx | 1.1.2 | MIT |
| openpyxl | 3.1.5 | MIT |
| rapidocr_onnxruntime | 1.4.4 | Apache-2.0 |

传递依赖（上述组件的依赖）同样适用各自的许可证，完整清单以
`pip freeze` 或 `backend/requirements.txt` 解析结果为准。

## 三、容器与运行时基础镜像

| 组件 | 版本 | 许可证 |
| --- | --- | --- |
| PostgreSQL | 16-alpine | PostgreSQL License |
| Neo4j Community | 5.26.0 | GPL-3.0 |
| nginx（前端容器运行时） | 1.27.5-alpine (Alpine 3.21) | BSD-2-Clause |
| Python（后端容器运行时） | 3.12.15-slim (Debian 13) | PSF-2.0 |
| Node.js（前端构建期运行时） | 22-slim | MIT |

数据库以独立容器运行、通过标准协议（PostgreSQL wire protocol / Bolt）交互，
不构成与 DocGraph 代码的衍生作品；但请勿将上述数据库源码并入本项目分发。
Python / Node.js / nginx 三个镜像只作为构建期或运行时基础镜像使用（镜像地址统一走
`public.ecr.aws/docker/library/*`），未修改其源码，未随本项目分发其代码。

## 四、开发期工具（不随产物分发）

TypeScript、Vite、`@vitejs/plugin-react`、`@types/*` 等仅在构建期使用，
不进入最终分发产物；其许可证分别为 MIT / Apache-2.0 类宽松协议。

---

## 五、需要留意的非宽松依赖

### PyMuPDF 1.25.1 — AGPL-3.0

PyMuPDF 采用 AGPL-3.0 与商业授权双许可（版权方 Artifex）。

- AGPL 是强 copyleft 协议，第 13 条对"通过网络提供服务的用户"额外要求提供
  对应源代码。
- 若 DocGraph 以后端服务形式对客户开放，AGPL 义务的适用范围需要单独评估，
  企业客户也可能在供应链审查中拒收 AGPL 组件。
- 备选：`pypdf`（BSD-3-Clause）、`pypdfium2`（Apache-2.0 / BSD-3-Clause）、
  已在本项目内的 `pdfplumber`（MIT，基于 pdfminer.six）。替换前需评估对
  扫描件渲染、页面截图与 bbox 定位的影响。
- 若要继续使用，建议向 Artifex 取得商业授权，或在法律意见下确认当前用法
  不构成对外分发。

### psycopg2-binary 2.9.10 — LGPL-3.0 + 例外

psycopg2 采用 LGPL-3.0 并附链接例外。以动态链接方式作为独立库使用通常可以接受。
如希望完全避开 LGPL，可迁移到 `psycopg`（psycopg 3）或 `asyncpg`（Apache-2.0）。

### Neo4j Community 5.26.0（容器）— GPL-3.0

作为独立进程通过 Bolt 协议访问，不属于链接或衍生；不要将 Neo4j 源码或类库
合并进本项目代码。企业版有独立商业许可，Community 版不包含企业特性。

### Pillow 11.0.0 — MIT-CMU

Pillow 采用 MIT-CMU 许可，并在其 LICENSE 中列出捆绑库（libjpeg、zlib、libtiff 等）
的声明，共约 57 KB。分发时请一并保留
`backend/.venv/Lib/site-packages/Pillow-11.0.0.dist-info/LICENSE`。

---

## 六、许可证全文

以下全文适用于上表中标注对应许可证的组件。

### MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

### ISC License（d3）

Copyright 2010-2023 Mike Bostock

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.

### BSD-2-Clause License（mammoth）

Copyright (c) 2013, Michael Williamson
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

### BSD-3-Clause License（uvicorn、httpx、python-dotenv）

Copyright (c) 2017-present, Encode OSS Ltd. All rights reserved.
（其余组件请以其包内 LICENSE 中的版权声明为准）

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

### Apache License 2.0（pdfjs-dist、openai、neo4j 驱动、python-multipart、openpyxl、rapidocr_onnxruntime）

                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS
