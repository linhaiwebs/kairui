#!/usr/bin/env python3
"""Replace old 3-step static pipeline with correct 4-step version in app-st212428.js"""

src = "C:/Users/Administrator/Desktop/kairui/frontend/static/js/app-st212428.js"

with open(src, "r", encoding="utf-8") as f:
    content = f.read()

old = """                                <!-- Static site timeline -->
                                <template v-if="pipelineStatuses[site.id]?.site_type === 'static'">
                                    <!-- Stage 1: DNS + 1Panel site -->
                                    <div class="timeline-icon" :class="pipelineStatuses[site.id]?.site_created ? 'active' : (pipelineStatuses[site.id]?.dns_resolved ? 'in-progress' : 'inactive')">
                                        <span class="material-symbols-outlined">dns</span>
                                    </div>
                                    <div class="timeline-line" :class="pipelineLineState(site, 'stage1')"></div>
                                    <!-- Stage 2: Files uploaded -->
                                    <div class="timeline-icon" :class="pipelineStatuses[site.id]?.files_uploaded ? 'active' : (pipelineStatuses[site.id]?.site_created ? 'in-progress' : 'inactive')">
                                        <span class="material-symbols-outlined">upload_file</span>
                                    </div>
                                    <div class="timeline-line" :class="pipelineLineState(site, 'stage2')"></div>
                                    <!-- Stage 3: Brand configured -->
                                    <div class="timeline-icon" :class="pipelineStatuses[site.id]?.brand_configured ? 'active' : (pipelineStatuses[site.id]?.files_uploaded ? 'in-progress' : 'inactive')">
                                        <i class="fas fa-cube"></i>
                                    </div>
                                </template>"""

new = """                                <!-- ===== STATIC SITE PIPELINE ===== -->
                                <template v-if="site.site_type === 'static'">
                                    <!-- ① DNS -->
                                    <div class="timeline-icon" :class="pipelineStatuses[site.id]?.dns_resolved ? 'active' : (site.status === 'deploying' ? 'in-progress' : 'inactive')"
                                         :title="pipelineStatuses[site.id]?.dns_resolved ? 'DNS已解析' : 'DNS解析'">
                                        <i class="fas fa-globe"></i>
                                    </div>
                                    <div class="timeline-line" :class="(pipelineStatuses[site.id]?.dns_resolved || site.status === 'deploying') ? 'active' : ''"></div>
                                    <!-- ② 网站 -->
                                    <div class="timeline-icon" :class="pipelineStatuses[site.id]?.site_created ? 'active' : (site.status === 'deploying' ? 'in-progress' : 'inactive')"
                                         :title="pipelineStatuses[site.id]?.site_created ? '1Panel网站已创建' : '创建1Panel网站'">
                                        <i class="fas fa-server"></i>
                                    </div>
                                    <div class="timeline-line" :class="pipelineStatuses[site.id]?.design_started ? 'active' : (pipelineStatuses[site.id]?.site_created ? 'in-progress' : '')"></div>
                                    <!-- ③ 设计 -->
                                    <div class="timeline-icon"
                                         :class="pipelineStatuses[site.id]?.design_complete ? 'active' :
                                            pipelineStatuses[site.id]?.design_generating ? 'in-progress' :
                                            pipelineStatuses[site.id]?.design_started ? 'in-progress' : 'inactive'"
                                         :title="pipelineStatuses[site.id]?.design_complete ? '设计完成' + (pipelineStatuses[site.id]?.design_label ? ' (' + pipelineStatuses[site.id].design_label + ')' : '') :
                                            pipelineStatuses[site.id]?.design_generating ? 'Stitch AI正在生成设计...' : '商城设计'">
                                        <i class="fas fa-paint-brush"></i>
                                    </div>
                                    <div class="timeline-line" :class="pipelineStatuses[site.id]?.files_uploaded ? 'active' : (pipelineStatuses[site.id]?.design_complete ? 'in-progress' : '')"></div>
                                    <!-- ④ 上线 -->
                                    <div class="timeline-icon" :class="pipelineStatuses[site.id]?.files_uploaded ? 'active' : 'inactive'"
                                         :title="pipelineStatuses[site.id]?.files_uploaded ? '站点已上线' : '上传文件'">
                                        <i class="fas fa-check-circle"></i>
                                    </div>
                                </template>"""

if old in content:
    content = content.replace(old, new)
    with open(src, "w", encoding="utf-8") as f:
        f.write(content)
    print("OK: pipeline replaced")
else:
    print("FAIL: old pattern not found")
    # debug
    idx = content.find("Static site timeline")
    if idx >= 0:
        print("Found at offset", idx)
        print(repr(content[idx:idx+300]))
    else:
        print("'Static site timeline' not found at all")
