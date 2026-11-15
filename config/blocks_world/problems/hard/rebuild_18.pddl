(define (problem rebuild-18)
  (:domain blocks-world)
  (:objects a b c d e f - block)
  (:init
    (clear c) (clear d) (hand-empty) (on a e) (on c f) (on d a) (on e b) (on-table b) (on-table f))
  (:goal (and (on a c) (on c d) (on e f) (on f b) (on-table b) (on-table d)))
)
