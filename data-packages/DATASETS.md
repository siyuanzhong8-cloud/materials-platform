# 催化栏目新增数据集（2026-11-13 导入，Asia/Seoul）

数据类型（data_class / 「数据类型」）：
- **实验数据** (experimental)：实测值。
- **计算数据** (computed)：第一性原理 DFT（文献 DFT、OC22、OC20、Catalysis-Hub 等）。
- **预测数据** (predicted)：UMA / 机器学习势推理（oer.sqlite 中 8,997 个预测构型）。

三类数据不混排、不互相排序比较；实验过电位按电流密度口径（η@3 / η@10 mA/cm²）分组。原始文件在 `/workspace/materials-platform/raw_datasets/catalysis/<dataset>/`（每个目录有 SOURCE.md 和逐文件 sha256），不随应用同步。

| 数据集 | 数据类型 | 位置 | 行数 |
|---|---|---|---|
| ACE-I 高通量 OER | 实验数据 | data/literature.sqlite，source `tri_ace_i_2020` | 8,482 |
| Caltech AcidOER Mn–Sb–Sn–Ti–Co | 实验数据 | data/literature.sqlite，source `caltech_acidoer_mnsbsntico_2023` | 245 |
| OC22 IS2RE-Total | 计算数据 | data/oc22_catalog.sqlite（新文件，独立） | 56,568 |

## 1. ACE-I（TRI / Caltech）
- 来源：https://data.matr.io/oer-catalysis/ ；https://data.caltech.edu/records/7b106-nf257 ；数据集 DOI 10.22002/D1.1345；论文 DOI 10.1039/C9SC05999G
- 许可：CC BY 4.0。署名：Gregoire, J.; Rohr, B.; Stein, H. S.; Guevarra, D.; Wang, Y. et al. OER catalyst data for active learning benchmarking study. CaltechDATA (2020), doi:10.22002/D1.1345；Rohr, B. et al. Chem. Sci. 2020, 11, 2696–2706.
- 原始文件：raw_datasets/catalysis/ace-i/tri_data_share.pck（764,549 B，md5 与仓库一致）
- 测量口径：**η@3 mA/cm²**（1 mm² 几何面积），pH 13（0.1 M NaOH + 0.25 M Na₂SO₄），2 h 老化；喷墨打印，400 °C/10 h 煅烧；扫描液滴池。存为 `experimental_overpotential_V` + `experimental_current_density_mA_cm2 = 3` + `measurement_basis = "η@3 mA/cm²"`，不会当作 η@10。
- 导入：4 个组分空间 3496 (A)、3851 (B)、3860 (C)、4098 (D)，分别 2121/2119/2121/2121 条。化学式写作金属原子分数加 Ox，例如 (Ni0.4Fe0.3Ce0.3)Ox；elements 为金属加 O。
- 元素顺序：pickle 内没有写列与元素的对应。按 Caltech MEAD 记录标题中的打印通道顺序推断，再用论文表 1 的 top 1% 阳离子数核对，四个空间都完全一致。
- 未导入：plate 3875（pickle 中有，但论文没有描述，元素未知）。

## 2. Caltech / JCAP AcidOER Mn–Sb–Sn–Ti–Co
- 来源：https://data.caltech.edu/records/tg041-j4g80 ；数据集 DOI 10.22002/tg041-j4g80；论文 DOI 10.1039/d3ta04899c
- 许可：CC0 1.0。建议引用：Zhou, L., & Gregoire, J. M. (2023). CaltechDATA, doi:10.22002/tg041-j4g80。
- 原始文件：raw_datasets/catalysis/caltech-acidoer/data/（OER/XRD/XRF，解压后 126 MB；zip 已删除，哈希见 SOURCE.md）
- 测量口径：O₂ 饱和的 1 M H₂SO₄，扫描液滴池；CV 1.23–1.79 V vs RHE，20 mV/s；200 s CA；几何面积 0.02 cm²。没有按浓度推算 pH 数值。
- 导入：oer_all_foms.csv 全部 245 行，包括正文 182 个组成（plate 5411–5414）和 ESI 中的 Mn–Sb–Co（plate 5029）63 个。字段 `experimental_activity`：Jmax_pre（J@1.79 V vs RHE，首次 CV，论文采用的活性指标）、Jmax_post、J_t20、J_t200，单位 mA/cm²。CA 电位取自原始数据（Ewe − erhe）。论文写的是 1.72 V，但原始数据中 plate 5413 约为 1.79 V，两者都保留。
- 未导入：过电位（数据集没有提供）；Tafel 斜率（CV 没有做 iR 和电容校正，不能直接算）；XRD 相分析和原始曲线只保留在原始目录中。

## 3. OC22 IS2RE-Total（计算数据）
- 来源：https://fair-chem.github.io/catalysts/datasets/oc22.html ；论文 DOI 10.1021/acscatal.2c05426
- 许可：CC BY 4.0。署名：Tran, R. et al. ACS Catal. 2023, 13, 3066–3084（Meta FAIR Chemistry）。
- DFT 设置：VASP，PAW，PBE+U（Materials Project U 值），自旋极化，截断能 500 eV，没有色散修正。
- 原始文件：raw_datasets/catalysis/oc22/is2res_total_train_val_test_lmdbs/（424 MB）和 oc22_metadata.pkl。109 MB 的 tar.gz 解压后已删除。
- 目录库 data/oc22_catalog.sqlite：`datasets` 1 行，记录泛函、U 值、标签、许可和 data_class=computed；`systems` 56,568 行，含 sid、split、bulk_id、bulk_formula、miller_index、adsorbate、adsorbate_class（O/OH/OOH/clean/other）、nads、slab_sid、natoms、elements、formula、total_energy_eV、lmdb 文件和键。
  - 按 split：train 45,890、val_id 2,624、val_ood 2,780、test_id 2,624、test_ood 2,650（test 没有公开能量）。
  - 按吸附物：O 9,710、OH 3,674、OOH 3,916、clean 18,437、other 20,831。
- 能量是总能量，不是吸附能，没有换算成 ΔG。没有接入火山图，也没有和 UMA 或实验值混用。
- 未下载：S2EF-Total LMDB（20.9 GB，超过单个文件 20 GB 的上限）；完整轨迹（36.2 GB）。

## 待下载（需要更大空间、注册或另行安排）
- OC22 S2EF-Total：https://dl.fbaipublicfiles.com/opencatalystproject/data/oc22/s2ef_total_train_val_test_lmdbs.tar.gz（20,863,705,269 B）
- OC22 轨迹：https://dl.fbaipublicfiles.com/opencatalystproject/data/oc22/oc22_trajectories.tar.gz（36,177,312,691 B）
- OC20 全集：约 1 TB，只列链接。
- OC25：按团队规则放入待下载。
- DigCat 等需要注册或点击同意条款的来源：不自动下载。
